// @vitest-environment jsdom
// 冒烟：验证 jsdom + Testing Library + tsx 测试链路端到端可用（G11 前置）。
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsPage } from "./Settings";

vi.mock("../core", () => ({ core: vi.fn(), setCorePath: vi.fn() }));

import { core } from "../core";

const coreMock = vi.mocked(core);

const CONFIG = {
  vlm: {
    model: "vl-global",
    base_url: "https://x.example/v1",
    api_key: "●●●●",
    format: "chat",
    custom_tier1: null,
    custom_tier2: null,
  },
  vlm_by_harness: { claude: { model: "vl-claude", base_url: "https://y.example/v1" } },
  routing: { unknown_default: "text_only" },
  vision_log: { enabled: true, retention_days: 7 },
};

describe("SettingsPage smoke", () => {
  beforeEach(() => {
    coreMock.mockReset();
    coreMock.mockImplementation(async (verb: string) =>
      verb === "config" ? JSON.parse(JSON.stringify(CONFIG)) : {},
    );
  });

  it("renders the VLM card and loads config fields", async () => {
    render(<SettingsPage lang="zh" status={null} refresh={vi.fn()} setLang={vi.fn()} />);
    expect(screen.getByText(/VLM（唯一必配）/)).toBeTruthy();
    expect(await screen.findByDisplayValue("vl-global")).toBeTruthy(); // config 动词数据落到表单
  });
});
