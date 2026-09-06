const { expect } = require("chai");
const { ethers } = require("hardhat");
const { loadFixture } = require("@nomicfoundation/hardhat-network-helpers");

const b32 = (s) => ethers.encodeBytes32String(s);
const KG = 1000n; // grams per kg -- everything on chain is grams

const Role = {
  None: 0, Beekeeper: 1, CollectionCentre: 2, FPO: 3, Processor: 4,
  Lab: 5, Brand: 6, Regulator: 7, Admin: 8,
};

// --- minimal sorted-pair merkle tree, matching OZ MerkleProof ---------------
// OZ hashes pairs commutatively (sorted), so the tree must too.
const hashPair = (a, b) => {
  const [x, y] = a < b ? [a, b] : [b, a];
  return ethers.keccak256(ethers.concat([x, y]));
};

function merkleRoot(leaves) {
  let level = [...leaves];
  while (level.length > 1) {
    const next = [];
    for (let i = 0; i < level.length; i += 2) {
      next.push(i + 1 < level.length ? hashPair(level[i], level[i + 1]) : level[i]);
    }
    level = next;
  }
  return level[0];
}

function merkleProof(leaves, index) {
  const proof = [];
  let level = [...leaves];
  let idx = index;
  while (level.length > 1) {
    const next = [];
    for (let i = 0; i < level.length; i += 2) {
      if (i + 1 < level.length) {
        if (i === idx || i + 1 === idx) proof.push(level[i === idx ? i + 1 : i]);
        next.push(hashPair(level[i], level[i + 1]));
      } else {
        next.push(level[i]);
      }
    }
    idx = Math.floor(idx / 2);
    level = next;
  }
  return proof;
}

// ---------------------------------------------------------------------------
async function deployFixture() {
  const [admin, beekeeper, centre, processor, lab, brand, regulator, relayer, outsider] =
    await ethers.getSigners();

  const registry = await (await ethers.getContractFactory("Registry")).deploy();
  const oracle = await (await ethers.getContractFactory("YieldOracle")).deploy(registry.target);
  const batches = await (await ethers.getContractFactory("HoneyBatch"))
    .deploy(registry.target, oracle.target, "https://aurabee.in/api/batch/{id}");
  const attest = await (await ethers.getContractFactory("Attestation")).deploy(registry.target);
  const seals = await (await ethers.getContractFactory("SealRegistry"))
    .deploy(registry.target, batches.target, attest.target);

  const reg = async (who, role, name) =>
    registry.registerActor(who.address, role, ethers.ZeroHash, name);

  await reg(beekeeper, Role.Beekeeper, "Ramesh Verma");
  await reg(centre, Role.CollectionCentre, "KVIC Sitapur");
  await reg(processor, Role.Processor, "Awadh Processing");
  await reg(lab, Role.Lab, "FARE Labs");
  await reg(brand, Role.Brand, "Awadh Naturals");
  await reg(regulator, Role.Regulator, "KVIC Cell");
  await registry.setRelayer(relayer.address, true);

  const apiaryId = b32("apiary-sitapur-01");
  await registry.registerApiary(apiaryId, beekeeper.address, b32("tuhw8"), 14, 2);

  const season = b32("2026-mustard");

  return {
    registry, oracle, batches, seals, attest,
    admin, beekeeper, centre, processor, lab, brand, regulator, relayer, outsider,
    apiaryId, season,
  };
}

// A passing lab report from an independent NABL lab. Seals cannot be issued
// without one, so most seal tests need this first.
async function labPass(attest, lab, relayer, batchId) {
  return attest.connect(relayer).attest(
    1 /* Batch */, batchId, 1 /* LabReport */, lab.address,
    ethers.keccak256(ethers.toUtf8Bytes("nabl-report.pdf")),
    0, true,
    [ethers.id("c4_pct_x100"), ethers.id("moisture_x100")],
    [40, 1810]
  );
}

// Publish an envelope with a P90 ceiling of `kg`.
async function publishEnvelope(oracle, apiaryId, season, kg, coverageBps = 1600) {
  return oracle.publishEnvelope(
    apiaryId, season,
    BigInt(kg) * KG,               // P90 -- the ceiling
    (BigInt(kg) * KG * 70n) / 100n, // P50, display only
    1762000000, 1772000000,
    ethers.keccak256(ethers.toUtf8Bytes("telemetry-window-merkle-root")),
    coverageBps,
    "yield-lgbm-0.1.0"
  );
}

// ===========================================================================
describe("Registry", function () {
  it("bootstraps the deployer as admin and relayer", async function () {
    const { registry, admin } = await loadFixture(deployFixture);
    expect(await registry.hasRole(admin.address, Role.Admin)).to.equal(true);
    expect(await registry.isRelayer(admin.address)).to.equal(true);
  });

  it("refuses self-registration", async function () {
    const { registry, outsider } = await loadFixture(deployFixture);
    await expect(
      registry.connect(outsider).registerActor(outsider.address, Role.Beekeeper, ethers.ZeroHash, "x")
    ).to.be.revertedWithCustomError(registry, "NotAdmin");
  });

  it("lets a relayer act for a beekeeper but not invent one", async function () {
    const { registry, relayer, beekeeper, outsider } = await loadFixture(deployFixture);
    expect(await registry.canActAs(relayer.address, beekeeper.address)).to.equal(true);
    // an unregistered actor cannot be acted for, even by a relayer
    expect(await registry.canActAs(relayer.address, outsider.address)).to.equal(false);
  });

  it("stops a deactivated actor from being acted for", async function () {
    const { registry, relayer, beekeeper } = await loadFixture(deployFixture);
    await registry.setActorActive(beekeeper.address, false);
    expect(await registry.canActAs(relayer.address, beekeeper.address)).to.equal(false);
  });
});

// ===========================================================================
describe("YieldOracle", function () {
  it("publishes an envelope and bumps the revision on re-publish", async function () {
    const { oracle, apiaryId, season } = await loadFixture(deployFixture);
    await publishEnvelope(oracle, apiaryId, season, 520);
    expect((await oracle.getEnvelope(apiaryId, season)).revision).to.equal(1);
    await publishEnvelope(oracle, apiaryId, season, 480);
    const env = await oracle.getEnvelope(apiaryId, season);
    expect(env.revision).to.equal(2);
    expect(env.maxGrams).to.equal(480n * KG);
  });

  it("rejects an envelope with no evidence hash", async function () {
    const { oracle, apiaryId, season } = await loadFixture(deployFixture);
    await expect(
      oracle.publishEnvelope(apiaryId, season, 100n * KG, 70n * KG,
        1762000000, 1772000000, ethers.ZeroHash, 1600, "m")
    ).to.be.revertedWithCustomError(oracle, "NoEvidence");
  });

  it("rejects publication from a non-oracle", async function () {
    const { oracle, outsider, apiaryId, season } = await loadFixture(deployFixture);
    await expect(
      publishEnvelope(oracle.connect(outsider), apiaryId, season, 500)
    ).to.be.revertedWithCustomError(oracle, "NotOracle");
  });
});

// ===========================================================================
describe("HoneyBatch :: invariant 1 -- yield-bound issuance", function () {
  it("mints a harvest inside the envelope", async function () {
    const { oracle, batches, beekeeper, relayer, apiaryId, season } =
      await loadFixture(deployFixture);
    await publishEnvelope(oracle, apiaryId, season, 520);

    await expect(
      batches.connect(relayer).mintHarvest(
        b32("batch-001"), apiaryId, season, 480n * KG, b32("mustard"), 1764000000)
    ).to.emit(batches, "BatchMinted");

    expect(await batches.balanceOf(beekeeper.address, BigInt(b32("batch-001")))).to.equal(480n * KG);
    expect(await batches.remainingInSeason(apiaryId, season)).to.equal(40n * KG);
  });

  it("REVERTS a declaration above the envelope -- the core guarantee", async function () {
    const { oracle, batches, relayer, apiaryId, season } = await loadFixture(deployFixture);
    await publishEnvelope(oracle, apiaryId, season, 520);

    await expect(
      batches.connect(relayer).mintHarvest(
        b32("batch-fraud"), apiaryId, season, 800n * KG, b32("mustard"), 1764000000)
    ).to.be.revertedWithCustomError(batches, "ExceedsYieldEnvelope")
      .withArgs(800n * KG, 520n * KG);
  });

  it("enforces the cap CUMULATIVELY across many small batches", async function () {
    // The subtle attack: ten declarations each comfortably under the ceiling
    // that together blow straight through it.
    const { oracle, batches, relayer, apiaryId, season } = await loadFixture(deployFixture);
    await publishEnvelope(oracle, apiaryId, season, 500);

    for (let i = 0; i < 5; i++) {
      await batches.connect(relayer).mintHarvest(
        b32(`batch-${i}`), apiaryId, season, 90n * KG, b32("mustard"), 1764000000);
    }
    expect(await batches.remainingInSeason(apiaryId, season)).to.equal(50n * KG);

    await expect(
      batches.connect(relayer).mintHarvest(
        b32("batch-over"), apiaryId, season, 90n * KG, b32("mustard"), 1764000000)
    ).to.be.revertedWithCustomError(batches, "ExceedsYieldEnvelope")
      .withArgs(90n * KG, 50n * KG);
  });

  it("refuses to mint at all when no envelope exists", async function () {
    const { batches, relayer, apiaryId, season } = await loadFixture(deployFixture);
    await expect(
      batches.connect(relayer).mintHarvest(
        b32("batch-x"), apiaryId, season, 1n * KG, b32("mustard"), 1764000000)
    ).to.be.revertedWithCustomError(batches, "NoEnvelope");
  });

  it("lets a regulator mint surplus, recording exactly what was unbacked", async function () {
    const { oracle, batches, regulator, apiaryId, season } = await loadFixture(deployFixture);
    await publishEnvelope(oracle, apiaryId, season, 500);

    await batches.connect(regulator).mintSurplus(
      b32("batch-surplus"), apiaryId, season, 620n * KG, b32("mustard"), 1764000000);

    const b = await batches.getBatch(b32("batch-surplus"));
    expect(b.mintedGrams).to.equal(620n * KG);
    expect(b.surplusGrams).to.equal(120n * KG); // the part telemetry does not support
  });

  it("does not let a beekeeper mint their own surplus", async function () {
    const { oracle, batches, relayer, apiaryId, season } = await loadFixture(deployFixture);
    await publishEnvelope(oracle, apiaryId, season, 500);
    await expect(
      batches.connect(relayer).mintSurplus(
        b32("b"), apiaryId, season, 900n * KG, b32("mustard"), 1764000000)
    ).to.be.revertedWithCustomError(batches, "NotAuthorised");
  });

  it("does not let a stranger mint against someone else's apiary", async function () {
    const { oracle, batches, outsider, apiaryId, season } = await loadFixture(deployFixture);
    await publishEnvelope(oracle, apiaryId, season, 500);
    await expect(
      batches.connect(outsider).mintHarvest(
        b32("b"), apiaryId, season, 10n * KG, b32("mustard"), 1764000000)
    ).to.be.revertedWithCustomError(batches, "NotAuthorised");
  });
});

// ===========================================================================
describe("HoneyBatch :: custody", function () {
  async function withBatch() {
    const f = await loadFixture(deployFixture);
    await publishEnvelope(f.oracle, f.apiaryId, f.season, 500);
    await f.batches.connect(f.relayer).mintHarvest(
      b32("batch-001"), f.apiaryId, f.season, 400n * KG, b32("mustard"), 1764000000);
    return f;
  }

  it("moves custody and emits provenance", async function () {
    const { batches, relayer, beekeeper, centre } = await withBatch();
    await expect(
      batches.connect(relayer).transferCustody(
        b32("batch-001"), beekeeper.address, centre.address, 400n * KG)
    ).to.emit(batches, "CustodyTransferred")
      .withArgs(b32("batch-001"), beekeeper.address, centre.address, 400n * KG);

    expect(await batches.balanceOf(centre.address, BigInt(b32("batch-001")))).to.equal(400n * KG);
  });

  it("refuses custody transfer to an unregistered party", async function () {
    const { batches, relayer, beekeeper, outsider } = await withBatch();
    await expect(
      batches.connect(relayer).transferCustody(
        b32("batch-001"), beekeeper.address, outsider.address, 10n * KG)
    ).to.be.revertedWithCustomError(batches, "NotAuthorised");
  });

  it("disables raw ERC-1155 transfers so honey cannot move without provenance",
    async function () {
      const { batches, beekeeper, centre } = await withBatch();
      await expect(
        batches.connect(beekeeper).safeTransferFrom(
          beekeeper.address, centre.address, BigInt(b32("batch-001")), 1n * KG, "0x")
      ).to.be.revertedWithCustomError(batches, "TransfersDisabled");
    });
});

// ===========================================================================
describe("HoneyBatch :: invariant 2 -- mass conservation", function () {
  async function atProcessor(grams = 400n * KG) {
    const f = await loadFixture(deployFixture);
    await publishEnvelope(f.oracle, f.apiaryId, f.season, 1000);
    await f.batches.connect(f.relayer).mintHarvest(
      b32("raw-001"), f.apiaryId, f.season, grams, b32("mustard"), 1764000000);
    await f.batches.connect(f.relayer).transferCustody(
      b32("raw-001"), f.beekeeper.address, f.processor.address, grams);
    return f;
  }

  it("allows processing with a plausible loss", async function () {
    const { batches, processor, relayer } = await atProcessor();
    await expect(
      batches.connect(relayer).processBatch(
        b32("raw-001"), b32("proc-001"), 400n * KG, 380n * KG, processor.address)
    ).to.emit(batches, "BatchProcessed")
      .withArgs(b32("raw-001"), b32("proc-001"), 400n * KG, 380n * KG, 20n * KG);

    expect(await batches.balanceOf(processor.address, BigInt(b32("proc-001"))))
      .to.equal(380n * KG);
  });

  it("REVERTS when a processor tries to create honey from nothing", async function () {
    const { batches, processor, relayer } = await atProcessor();
    await expect(
      batches.connect(relayer).processBatch(
        b32("raw-001"), b32("proc-001"), 400n * KG, 450n * KG, processor.address)
    ).to.be.revertedWithCustomError(batches, "MassNotConserved")
      .withArgs(450n * KG, 400n * KG);
  });

  it("REVERTS on an implausible declared loss", async function () {
    // 400 -> 200 kg is a 50% loss. Real filtering loses a few percent.
    const { batches, processor, relayer } = await atProcessor();
    await expect(
      batches.connect(relayer).processBatch(
        b32("raw-001"), b32("proc-001"), 400n * KG, 200n * KG, processor.address)
    ).to.be.revertedWithCustomError(batches, "LossTooHigh").withArgs(5000n, 1500);
  });

  it("blends several batches and records every constituent", async function () {
    const f = await loadFixture(deployFixture);
    const other = b32("apiary-sitapur-02");
    await f.registry.registerApiary(other, f.beekeeper.address, b32("tuhw9"), 10, 2);
    await publishEnvelope(f.oracle, f.apiaryId, f.season, 1000);
    await publishEnvelope(f.oracle, other, f.season, 1000);

    await f.batches.connect(f.relayer).mintHarvest(
      b32("raw-A"), f.apiaryId, f.season, 300n * KG, b32("mustard"), 1764000000);
    await f.batches.connect(f.relayer).mintHarvest(
      b32("raw-B"), other, f.season, 200n * KG, b32("litchi"), 1764000000);
    for (const id of [b32("raw-A"), b32("raw-B")]) {
      const amt = id === b32("raw-A") ? 300n * KG : 200n * KG;
      await f.batches.connect(f.relayer).transferCustody(
        id, f.beekeeper.address, f.processor.address, amt);
    }

    await f.batches.connect(f.relayer).blend(
      b32("blend-001"), [b32("raw-A"), b32("raw-B")], [300n * KG, 200n * KG],
      495n * KG, f.processor.address);

    const comp = await f.batches.getComposition(b32("blend-001"));
    expect(comp.length).to.equal(2);
    expect(comp[0].grams).to.equal(300n * KG);
    expect(comp[1].grams).to.equal(200n * KG);
    // 300:200 -> the EU declaration reads 60% / 40%, computed off chain from this
    const total = comp[0].grams + comp[1].grams;
    expect((comp[0].grams * 100n) / total).to.equal(60n);

    // inputs are fully consumed
    expect(await f.batches.balanceOf(f.processor.address, BigInt(b32("raw-A")))).to.equal(0n);
  });

  it("REVERTS a blend that outputs more than went in", async function () {
    const { batches, processor, relayer } = await atProcessor(300n * KG);
    await expect(
      batches.connect(relayer).blend(
        b32("blend-x"), [b32("raw-001")], [300n * KG], 400n * KG, processor.address)
    ).to.be.revertedWithCustomError(batches, "MassNotConserved");
  });

  it("does not let a non-processor process", async function () {
    const { batches, relayer, centre } = await atProcessor();
    await expect(
      batches.connect(relayer).processBatch(
        b32("raw-001"), b32("p"), 100n * KG, 95n * KG, centre.address)
    ).to.be.revertedWithCustomError(batches, "NotAuthorised");
  });
});

// ===========================================================================
describe("SealRegistry", function () {
  async function packed() {
    const f = await loadFixture(deployFixture);
    await publishEnvelope(f.oracle, f.apiaryId, f.season, 1000);
    await f.batches.connect(f.relayer).mintHarvest(
      b32("raw-001"), f.apiaryId, f.season, 500n * KG, b32("mustard"), 1764000000);

    const batchId = b32("raw-001");
    // four jars, 500 g each
    const jars = [0, 1, 2, 3].map((i) => ({
      serial: b32(`AB-2026-000${i}`),
      secret: b32(`secret-${i}`),
    }));
    const leaves = await Promise.all(
      jars.map((j) => f.seals.leafOf(batchId, j.serial, j.secret))
    );
    const root = merkleRoot(leaves);
    await labPass(f.attest, f.lab, f.relayer, batchId);
    await f.seals.connect(f.relayer).issueSeals(
      batchId, root, jars.length, 500, f.processor.address);

    return { ...f, batchId, jars, leaves, root };
  }

  it("verifies a genuine jar against the anchored root", async function () {
    const { seals, batchId, jars, leaves } = await packed();
    const proof = merkleProof(leaves, 2);
    expect(await seals.verifySeal(batchId, jars[2].serial, jars[2].secret, proof))
      .to.equal(true);
  });

  it("rejects a cloned QR that lacks the scratch-off secret", async function () {
    // The attacker photographed the label, so they have the serial but not the
    // code under the scratch-off. This is the entire reason seals are per jar.
    const { seals, batchId, jars, leaves } = await packed();
    const proof = merkleProof(leaves, 1);
    expect(await seals.verifySeal(batchId, jars[1].serial, b32("guessed"), proof))
      .to.equal(false);
  });

  it("rejects a jar that was never part of the run", async function () {
    const { seals, batchId, leaves } = await packed();
    const proof = merkleProof(leaves, 0);
    expect(await seals.verifySeal(batchId, b32("AB-2026-9999"), b32("whatever"), proof))
      .to.equal(false);
  });

  it("exposes claimed mass so over-packing is arithmetic, not opinion", async function () {
    const { seals, batchId } = await packed();
    expect(await seals.claimedGrams(batchId)).to.equal(2000n); // 4 jars x 500 g
  });

  it("refuses a second issuance for the same batch", async function () {
    const { seals, relayer, processor, batchId } = await packed();
    await expect(
      seals.connect(relayer).issueSeals(batchId, ethers.ZeroHash, 10, 500, processor.address)
    ).to.be.revertedWithCustomError(seals, "AlreadyIssued");
  });

  // --- the output cap: syrup added on the packing line has no seal to sell under
  it("REFUSES to issue more jars than the batch holds", async function () {
    const f = await loadFixture(deployFixture);
    await publishEnvelope(f.oracle, f.apiaryId, f.season, 1000);
    await f.batches.connect(f.relayer).mintHarvest(
      b32("raw-cap"), f.apiaryId, f.season, 100n * KG, b32("mustard"), 1764000000);
    await labPass(f.attest, f.lab, f.relayer, b32("raw-cap"));

    // 100 kg batch, but the packer claims 400 jars x 500 g = 200 kg
    await expect(
      f.seals.connect(f.relayer).issueSeals(
        b32("raw-cap"), ethers.ZeroHash, 400, 500, f.processor.address)
    ).to.be.revertedWithCustomError(f.seals, "OverPacking")
      .withArgs(200000n, 100000n);
  });

  it("allows packing right up to the batch mass", async function () {
    const f = await leadFixtureWithLab(100n * KG);
    // exactly 200 jars x 500 g = 100 kg
    await expect(
      f.seals.connect(f.relayer).issueSeals(
        b32("raw-cap"), ethers.ZeroHash, 200, 500, f.processor.address)
    ).to.emit(f.seals, "SealsIssued");
  });

  // --- the lab gate: no independent pass, no QR codes
  it("REFUSES to issue seals without a passing lab report", async function () {
    const f = await loadFixture(deployFixture);
    await publishEnvelope(f.oracle, f.apiaryId, f.season, 1000);
    await f.batches.connect(f.relayer).mintHarvest(
      b32("raw-nolab"), f.apiaryId, f.season, 100n * KG, b32("mustard"), 1764000000);

    await expect(
      f.seals.connect(f.relayer).issueSeals(
        b32("raw-nolab"), ethers.ZeroHash, 10, 500, f.processor.address)
    ).to.be.revertedWithCustomError(f.seals, "NoPassingLabReport");
  });

  it("REFUSES seals when the lab report FAILED", async function () {
    const f = await loadFixture(deployFixture);
    await publishEnvelope(f.oracle, f.apiaryId, f.season, 1000);
    await f.batches.connect(f.relayer).mintHarvest(
      b32("raw-fail"), f.apiaryId, f.season, 100n * KG, b32("mustard"), 1764000000);
    // lab tested it and it did not pass
    await f.attest.connect(f.relayer).attest(
      1, b32("raw-fail"), 1, f.lab.address, ethers.ZeroHash, 0, false, [], []);

    await expect(
      f.seals.connect(f.relayer).issueSeals(
        b32("raw-fail"), ethers.ZeroHash, 10, 500, f.processor.address)
    ).to.be.revertedWithCustomError(f.seals, "NoPassingLabReport");
  });

  it("REFUSES seals once a lab report is revoked", async function () {
    const f = await leadFixtureWithLab(100n * KG);
    await f.attest.connect(f.regulator).revoke(0, "retested, failed SMR");
    await expect(
      f.seals.connect(f.relayer).issueSeals(
        b32("raw-cap"), ethers.ZeroHash, 10, 500, f.processor.address)
    ).to.be.revertedWithCustomError(f.seals, "NoPassingLabReport");
  });

  // A brand cannot clear its own honey for packing.
  it("does not accept a self-issued lab report from the brand", async function () {
    const f = await loadFixture(deployFixture);
    await expect(
      f.attest.connect(f.relayer).attest(
        1, b32("raw-x"), 1, f.brand.address, ethers.ZeroHash, 0, true, [], [])
    ).to.be.revertedWithCustomError(f.attest, "NotAuthorised");
  });
});

// Batch minted, lab-passed, ready to pack.
async function leadFixtureWithLab(grams) {
  const f = await loadFixture(deployFixture);
  await publishEnvelope(f.oracle, f.apiaryId, f.season, 1000);
  await f.batches.connect(f.relayer).mintHarvest(
    b32("raw-cap"), f.apiaryId, f.season, grams, b32("mustard"), 1764000000);
  await labPass(f.attest, f.lab, f.relayer, b32("raw-cap"));
  return f;
}

// ===========================================================================
describe("Attestation", function () {
  it("records a lab report with structured metrics", async function () {
    const { attest, lab, relayer } = await loadFixture(deployFixture);
    const subject = b32("raw-001");
    const docHash = ethers.keccak256(ethers.toUtf8Bytes("lab-report.pdf"));

    await attest.connect(relayer).attest(
      1 /* Batch */, subject, 1 /* LabReport */, lab.address, docHash,
      0, true,
      [ethers.id("c4_pct_x100"), ethers.id("moisture_x100")],
      [40, 1820] // 0.40% C4, 18.20% moisture -- scaled, because no floats on chain
    );

    expect(await attest.hasValid(subject, 1)).to.equal(true);
    expect(await attest.metric(0, ethers.id("moisture_x100"))).to.equal(1820);
  });

  it("does not let a brand attest to its own purity", async function () {
    const { attest, brand, relayer } = await loadFixture(deployFixture);
    await expect(
      attest.connect(relayer).attest(
        1, b32("raw-001"), 1, brand.address, ethers.ZeroHash, 0, true, [], [])
    ).to.be.revertedWithCustomError(attest, "NotAuthorised");
  });

  it("treats an expired attestation as invalid", async function () {
    const { attest, lab, relayer } = await loadFixture(deployFixture);
    const subject = b32("raw-002");
    await attest.connect(relayer).attest(
      1, subject, 1, lab.address, ethers.ZeroHash, 1000 /* long past */, true, [], []);
    expect(await attest.hasValid(subject, 1)).to.equal(false);
  });

  it("revokes without erasing history", async function () {
    const { attest, lab, regulator, relayer } = await loadFixture(deployFixture);
    const subject = b32("raw-003");
    await attest.connect(relayer).attest(
      1, subject, 1, lab.address, ethers.ZeroHash, 0, true, [], []);
    expect(await attest.hasValid(subject, 1)).to.equal(true);

    await attest.connect(regulator).revoke(0, "retested, failed SMR");
    expect(await attest.hasValid(subject, 1)).to.equal(false);
    // the record is still there, marked revoked -- history is the point
    expect((await attest.getRecord(0)).revoked).to.equal(true);
    expect(await attest.totalRecords()).to.equal(1n);
  });
});
