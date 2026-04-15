import { assertEquals } from "https://deno.land/std@0.224.0/assert/mod.ts";
import { parseParams } from "../url.js";

Deno.test("parseParams: extracts key-value pairs from hash", () => {
  const params = parseParams("#/types?spec=consensus-specs&kind=class");
  assertEquals(params.spec, "consensus-specs");
  assertEquals(params.kind, "class");
});

Deno.test("parseParams: returns empty object for no params", () => {
  const params = parseParams("#/types");
  assertEquals(Object.keys(params).length, 0);
});

Deno.test("parseParams: handles URL-encoded values", () => {
  const params = parseParams("#/types?q=hello%20world");
  assertEquals(params.q, "hello world");
});

Deno.test("parseParams: handles empty hash", () => {
  const params = parseParams("");
  assertEquals(Object.keys(params).length, 0);
});

Deno.test("parseParams: handles hash with just question mark", () => {
  const params = parseParams("#/?");
  assertEquals(Object.keys(params).length, 0);
});
