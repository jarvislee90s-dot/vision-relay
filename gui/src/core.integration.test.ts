// core.ts 集成测试：parseEnvelope 对真实 envelope 变体的处理 + core() invoke→parse→data 完整链路。
// 与 core.test.ts（基础单测）的区别：mock @tauri-apps/api/core，验证 Tauri 契约两侧
// （args 拼装 / stdin JSON 序列化 / 契约版本不匹配抛错）真的在链路里生效。
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

import { invoke } from "@tauri-apps/api/core";
import { CONTRACT_VERSION, core, detectCore, parseEnvelope, setCorePath } from "./core";

const CORE_EXE = "C:/fake/vision-relay.exe";
const mockedInvoke = vi.mocked(invoke);

beforeEach(() => {
  vi.clearAllMocks();
  setCorePath(CORE_EXE); // 显式路径，core() 不再走 which_core
});

describe("parseEnvelope — 真实 envelope 变体", () => {
  it("accepts nested data and returns it as-is", () => {
    const data = { models: [{ harness: "claude", provider: "?", model: "m1", value: null }] };
    expect(parseEnvelope({ contract_version: 1, ok: true, data })).toBe(data);
  });

  it("tolerates extra top-level fields (forward-compatible)", () => {
    expect(parseEnvelope({ contract_version: 1, ok: true, data: 7, extra: "ignored" })).toBe(7);
  });

  it("ok:false surfaces data.error text", () => {
    expect(() => parseEnvelope({ contract_version: 1, ok: false, data: { error: "value must be image|text_only|null" } }))
      .toThrowError(/image\|text_only\|null/);
  });

  it("ok:false without error field falls back to generic message", () => {
    expect(() => parseEnvelope({ contract_version: 1, ok: false, data: {} })).toThrowError(/core verb failed/);
  });

  it("contract mismatch reports both versions (upgrade guidance)", () => {
    expect(() => parseEnvelope({ contract_version: 2, ok: true, data: {} })).toThrowError(
      new RegExp(`contract mismatch: core=2, gui=${CONTRACT_VERSION}`),
    );
  });

  it("missing data key throws", () => {
    expect(() => parseEnvelope({ contract_version: 1, ok: true })).toThrowError(/data/);
  });

  it("non-object payloads (number / string / null / array) throw", () => {
    for (const bad of [42, "json?", null, [1, 2]]) {
      expect(() => parseEnvelope(bad)).toThrowError(/not JSON/);
    }
  });
});

describe("core() — invoke → parseEnvelope → data 完整链路", () => {
  it("builds [verb, --json, ...args], JSON-stringifies stdin, parses envelope", async () => {
    mockedInvoke.mockResolvedValue(JSON.stringify({ contract_version: 1, ok: true, data: { service_alive: true } }));
    const out = await core<{ service_alive: boolean }>("status");
    expect(out).toEqual({ service_alive: true });
    expect(mockedInvoke).toHaveBeenCalledWith("run_core", {
      corePath: CORE_EXE,
      args: ["status", "--json"],
      stdin: null,
    });
  });

  it("passes extra args after --json (probe --harness ...)", async () => {
    mockedInvoke.mockResolvedValue(JSON.stringify({ contract_version: 1, ok: true, data: { result: "image" } }));
    await core("probe", { args: ["--harness", "qwen-code", "--model", "m1"] });
    expect(mockedInvoke).toHaveBeenCalledWith("run_core", {
      corePath: CORE_EXE,
      args: ["probe", "--json", "--harness", "qwen-code", "--model", "m1"],
      stdin: null,
    });
  });

  it("serializes stdin payload exactly once as JSON (Windows 引号地狱解法)", async () => {
    mockedInvoke.mockResolvedValue(JSON.stringify({ contract_version: 1, ok: true, data: { updated: 2 } }));
    const payload = [{ harness: "claude", provider: "供应·商", model: "m/m", value: "image" }];
    await core("models-set", { stdin: payload });
    const call = mockedInvoke.mock.calls[0][1] as { stdin: string | null };
    expect(call.stdin).toBe(JSON.stringify(payload));
    expect(JSON.parse(call.stdin as string)).toEqual(payload);
  });

  it("rejects when core returns contract mismatch", async () => {
    mockedInvoke.mockResolvedValue(JSON.stringify({ contract_version: 99, ok: true, data: {} }));
    await expect(core("status")).rejects.toThrow(/contract mismatch/);
  });

  it("rejects with core error text when envelope ok:false", async () => {
    mockedInvoke.mockResolvedValue(JSON.stringify({ contract_version: 1, ok: false, data: { error: "unknown relay 'ghost'" } }));
    await expect(core("relay-set", { stdin: { name: "ghost" } })).rejects.toThrow(/unknown relay/);
  });

  it("propagates spawn errors from rust (core missing / no output)", async () => {
    mockedInvoke.mockRejectedValue("spawn C:/fake/vision-relay.exe: 系统找不到指定的文件。");
    await expect(core("status")).rejects.toThrow(/spawn/);
  });
});

describe("detectCore — 核心探测与缓存", () => {
  it("explicit path is used and cached without invoking which_core", async () => {
    setCorePath(null);
    expect(await detectCore(CORE_EXE)).toBe(CORE_EXE);
    expect(mockedInvoke).not.toHaveBeenCalled();
    expect(await detectCore()).toBe(CORE_EXE); // 命中缓存
  });

  it("falls back to which_core; null result throws friendly guidance", async () => {
    setCorePath(null);
    mockedInvoke.mockResolvedValue(null);
    await expect(detectCore()).rejects.toThrow(/找不到 vision-relay 核心/);
    expect(mockedInvoke).toHaveBeenCalledWith("which_core");

    setCorePath(null);
    mockedInvoke.mockResolvedValue("/usr/local/bin/vision-relay");
    expect(await detectCore()).toBe("/usr/local/bin/vision-relay");
  });
});
