/**
 * Deploys the AuraBee contract set and writes an address book the Python API
 * reads at startup.
 *
 *   npx hardhat node                    # terminal 1
 *   npm run chain:deploy                # terminal 2
 *
 * The address book lands at packages/contracts/deployments/<network>.json and
 * carries the ABIs alongside the addresses, so the API needs no build step of
 * its own and cannot drift onto a stale ABI.
 */
const fs = require("fs");
const path = require("path");
const { ethers, network, artifacts } = require("hardhat");

const NAMES = ["Registry", "YieldOracle", "HoneyBatch", "SealRegistry", "Attestation"];
const TOKEN_URI = process.env.BATCH_TOKEN_URI || "https://aurabee.in/api/batch/{id}";

async function main() {
  const [deployer] = await ethers.getSigners();
  console.log(`network  : ${network.name}`);
  console.log(`deployer : ${deployer.address}`);
  console.log(`balance  : ${ethers.formatEther(await ethers.provider.getBalance(deployer.address))}\n`);

  const registry = await (await ethers.getContractFactory("Registry")).deploy();
  await registry.waitForDeployment();
  console.log(`Registry      ${registry.target}`);

  const oracle = await (await ethers.getContractFactory("YieldOracle")).deploy(registry.target);
  await oracle.waitForDeployment();
  console.log(`YieldOracle   ${oracle.target}`);

  const batches = await (await ethers.getContractFactory("HoneyBatch"))
    .deploy(registry.target, oracle.target, TOKEN_URI);
  await batches.waitForDeployment();
  console.log(`HoneyBatch    ${batches.target}`);

  // Attestation first: SealRegistry needs it, because seals cannot be issued
  // without a passing lab report.
  const attest = await (await ethers.getContractFactory("Attestation")).deploy(registry.target);
  await attest.waitForDeployment();
  console.log(`Attestation   ${attest.target}`);

  const seals = await (await ethers.getContractFactory("SealRegistry"))
    .deploy(registry.target, batches.target, attest.target);
  await seals.waitForDeployment();
  console.log(`SealRegistry  ${seals.target}`);

  const addresses = {
    Registry: registry.target,
    YieldOracle: oracle.target,
    HoneyBatch: batches.target,
    SealRegistry: seals.target,
    Attestation: attest.target,
  };

  const book = {
    network: network.name,
    chainId: Number((await ethers.provider.getNetwork()).chainId),
    deployer: deployer.address,
    deployedAt: new Date().toISOString(),
    addresses,
    abis: {},
  };
  for (const name of NAMES) {
    book.abis[name] = (await artifacts.readArtifact(name)).abi;
  }

  const dir = path.join(__dirname, "..", "deployments");
  fs.mkdirSync(dir, { recursive: true });
  const out = path.join(dir, `${network.name}.json`);
  fs.writeFileSync(out, JSON.stringify(book, null, 2));
  console.log(`\naddress book -> ${path.relative(process.cwd(), out)}`);

  // The deployer is admin + relayer + oracle out of the constructor. In a real
  // cluster these are three separate keys held by three separate parties; for
  // local development one key is fine and saying so beats pretending otherwise.
  console.log("\ndeployer holds admin + relayer + oracle rights (development only)");
}

main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
