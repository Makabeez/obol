// Register Obol's on-chain identity in the canonical ERC-8004 IdentityRegistry on Arc
// mainnet (verified live: register(string) present on the implementation).
//
//   node --env-file=.env register-agent.mjs <metadataURI>          # dry run
//   node --env-file=.env register-agent.mjs <metadataURI> --send
// metadataURI: e.g. https://raw.githubusercontent.com/Makabeez/obol/main/agent.json
import { createPublicClient, createWalletClient, http, parseAbi, parseEventLogs } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { arc } from "viem/chains";

const IDENTITY = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432";
const abi = parseAbi([
  "function register(string agentURI) returns (uint256)",
  "function balanceOf(address owner) view returns (uint256)",
  "function tokenURI(uint256 id) view returns (string)",
  "event Transfer(address indexed from, address indexed to, uint256 indexed tokenId)",
]);
const uri = process.argv[2];
const send = process.argv.includes("--send");
if (!uri || !/^(https|ipfs):\/\//.test(uri)) throw new Error("usage: register-agent.mjs <https|ipfs metadata URI> [--send]");

const account = privateKeyToAccount(process.env.BUYER_PRIVATE_KEY);
const rpc = http(process.env.ARC_RPC || "https://rpc.mainnet.arc.io");
const pub = createPublicClient({ chain: arc, transport: rpc });
const wal = createWalletClient({ account, chain: arc, transport: rpc });

const owned = await pub.readContract({ address: IDENTITY, abi, functionName: "balanceOf", args: [account.address] });
console.log(`agent ${account.address} already owns ${owned} ERC-8004 identities`);
if (owned > 0n) console.log("NOTE: an identity already exists for this key; registering again creates a second one.");
const res = await (await fetch(uri.replace(/^ipfs:\/\//, "https://ipfs.io/ipfs/"))).text();
JSON.parse(res); // metadata must be valid JSON before we publish it on-chain
console.log("metadata OK:", uri);
if (!send) { console.log("Dry run. Re-run with --send."); process.exit(0); }

const hash = await wal.writeContract({ address: IDENTITY, abi, functionName: "register", args: [uri] });
const rcpt = await pub.waitForTransactionReceipt({ hash });
const [ev] = parseEventLogs({ abi, logs: rcpt.logs, eventName: "Transfer" });
console.log(`registered agentId ${ev?.args?.tokenId}  tx https://explorer.arc.io/tx/${hash}`);
