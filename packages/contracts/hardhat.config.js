require("@nomicfoundation/hardhat-toolbox");

/**
 * Targets, in order of how we use them:
 *   hardhat   - in-process, for tests
 *   localhost - `npm run chain:node`, what the API talks to in development
 *   besu      - permissioned IBFT2, the production target (KVIC/NBB validators)
 *   amoy      - Polygon testnet, for a public demo link
 *
 * All four run the SAME Solidity. That is the entire reason we picked Besu
 * over Fabric: one codebase, several deployment stories.
 */
module.exports = {
  solidity: {
    version: "0.8.28",
    settings: {
      optimizer: { enabled: true, runs: 200 },
      // OpenZeppelin 5.6 uses the `mcopy` opcode, so Cancun is not optional.
      // Deployment targets must therefore be Cancun-capable: Besu >= 24.x and
      // Polygon Amoy both are. Pinned explicitly rather than left to the solc
      // default so a future compiler bump cannot silently change the target.
      evmVersion: "cancun",
    },
  },
  networks: {
    hardhat: { chainId: 31337 },
    localhost: { url: "http://127.0.0.1:8545", chainId: 31337 },
    besu: {
      url: process.env.BESU_RPC_URL || "http://127.0.0.1:8550",
      chainId: Number(process.env.BESU_CHAIN_ID || 1337),
      // Besu IBFT2 is a zero-gas network; the relayer needs no funding
      gasPrice: 0,
      accounts: process.env.RELAYER_PRIVATE_KEY ? [process.env.RELAYER_PRIVATE_KEY] : [],
    },
    amoy: {
      url: process.env.AMOY_RPC_URL || "https://rpc-amoy.polygon.technology",
      chainId: 80002,
      accounts: process.env.RELAYER_PRIVATE_KEY ? [process.env.RELAYER_PRIVATE_KEY] : [],
    },
  },
  paths: { sources: "./contracts", tests: "./test", artifacts: "./artifacts" },
};
