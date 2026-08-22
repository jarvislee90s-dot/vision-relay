import { describe, expect, it } from "vitest";
import { parseEnvelope, CONTRACT_VERSION } from "./core";

describe("parseEnvelope", () => {
  it("accepts matching contract and returns data", () => {
    expect(CONTRACT_VERSION).toBe(1);
    expect(parseEnvelope({ contract_version: 1, ok: true, data: { x: 2 } })).toEqual({ x: 2 });
  });
  it("throws on ok:false with error text", () => {
    expect(() => parseEnvelope({ contract_version: 1, ok: false, data: { error: "bad" } })).toThrowError(/bad/);
  });
  it("throws on contract mismatch", () => {
    expect(() => parseEnvelope({ contract_version: 2, ok: true, data: {} })).toThrowError(/contract/);
  });
  it("throws on non-json stdout", () => {
    expect(() => parseEnvelope(JSON.parse('{"contract_version":1,"ok":true}'))).toThrowError(/data/);
  });
});
