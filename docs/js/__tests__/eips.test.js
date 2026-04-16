import { assertEquals, assert } from "https://deno.land/std@0.208.0/assert/mod.ts";
import { getEipsByFork, getItemsBySpec, getUniqueForks } from "../eip-utils.js";

const mockEipIndex = {
  "7549": {
    number: 7549,
    title: "Move committee index outside Attestation",
    authors: "dapplion (@dapplion)",
    status: "Final",
    category: "Core",
    fork: "electra",
    items: [
      { name: "Attestation", kind: "class", spec: "consensus-specs", change: "new" },
      { name: "AggregateAndProof", kind: "class", spec: "consensus-specs", change: "modified" },
    ],
    summary: { new: 1, modified: 1, total: 2, specs: ["consensus-specs"] },
  },
  "4844": {
    number: 4844,
    title: "Shard Blob Transactions",
    status: "Final",
    fork: "deneb",
    items: [
      { name: "BeaconBlockBody", kind: "class", spec: "consensus-specs", change: "new" },
      { name: "ExecutionPayload", kind: "class", spec: "consensus-specs", change: "modified" },
    ],
    summary: { new: 1, modified: 1, total: 2, specs: ["consensus-specs"] },
  },
  "7732": {
    number: 7732,
    title: "Enshrined Proposer-Builder Separation",
    status: "Draft",
    fork: "gloas",
    items: [
      { name: "BeaconBlockBody", kind: "class", spec: "consensus-specs", change: "modified" },
      { name: "PayloadAttestationMessage", kind: "class", spec: "consensus-specs", change: "new" },
      { name: "SignedExecutionPayloadBid", kind: "class", spec: "builder-specs", change: "new" },
    ],
    summary: { new: 2, modified: 1, total: 3, specs: ["builder-specs", "consensus-specs"] },
  },
};

Deno.test("getEipsByFork groups EIPs by their introduction fork", () => {
  const byFork = getEipsByFork(mockEipIndex);
  assertEquals(Object.keys(byFork).sort(), ["deneb", "electra", "gloas"]);
  assertEquals(byFork["electra"].length, 1);
  assertEquals(byFork["electra"][0].number, 7549);
  assertEquals(byFork["deneb"].length, 1);
  assertEquals(byFork["deneb"][0].number, 4844);
});

Deno.test("getEipsByFork sorts EIPs by number within fork", () => {
  const index = {
    ...mockEipIndex,
    "7251": { ...mockEipIndex["7549"], number: 7251, fork: "electra" },
  };
  const byFork = getEipsByFork(index);
  assertEquals(byFork["electra"][0].number, 7251);
  assertEquals(byFork["electra"][1].number, 7549);
});

Deno.test("getItemsBySpec groups items by spec", () => {
  const bySpec = getItemsBySpec(mockEipIndex["7732"]);
  assertEquals(Object.keys(bySpec).sort(), ["builder-specs", "consensus-specs"]);
  assertEquals(bySpec["consensus-specs"].length, 2);
  assertEquals(bySpec["builder-specs"].length, 1);
});

Deno.test("getUniqueForks returns forks ordered by FORK_ORDER", () => {
  const forks = getUniqueForks(mockEipIndex);
  const denebIdx = forks.indexOf("deneb");
  const electraIdx = forks.indexOf("electra");
  const gloasIdx = forks.indexOf("gloas");
  assert(denebIdx < electraIdx, "deneb should come before electra");
  assert(electraIdx < gloasIdx, "electra should come before gloas");
});

Deno.test("getUniqueForks includes non-standard fork names at end", () => {
  const index = {
    ...mockEipIndex,
    "7928": { ...mockEipIndex["4844"], number: 7928, fork: "eip7928" },
  };
  const forks = getUniqueForks(index);
  assert(forks.includes("eip7928"), "should include eip7928 fork");
  assertEquals(forks[forks.length - 1], "eip7928");
});

Deno.test("EIP summary counts match items", () => {
  for (const eip of Object.values(mockEipIndex)) {
    const newCount = eip.items.filter(i => i.change === "new").length;
    const modCount = eip.items.filter(i => i.change === "modified").length;
    assertEquals(eip.summary.new, newCount);
    assertEquals(eip.summary.modified, modCount);
    assertEquals(eip.summary.total, eip.items.length);
  }
});

Deno.test("getEipsByFork returns empty object for empty index", () => {
  const byFork = getEipsByFork({});
  assertEquals(Object.keys(byFork).length, 0);
});

Deno.test("getUniqueForks returns empty array for empty index", () => {
  const forks = getUniqueForks({});
  assertEquals(forks.length, 0);
});
