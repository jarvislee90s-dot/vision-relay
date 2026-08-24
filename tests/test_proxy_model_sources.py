"""model_sources: 工具档案只读层(spec §5 模型矩阵来源)。密钥绝不读取。"""

import json
import sqlite3

from vision_relay import model_sources as ms


def _mk_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE providers (
            id TEXT NOT NULL, app_type TEXT NOT NULL, name TEXT NOT NULL,
            settings_config TEXT NOT NULL, is_current INTEGER,
            sort_index INTEGER, PRIMARY KEY (id, app_type))"""
    )
    conn.executemany("INSERT INTO providers VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_ccswitch_matrix_claude_env_models(tmp_path, monkeypatch):
    db = tmp_path / "cc-switch.db"
    _mk_db(
        db,
        [
            (
                "a1",
                "claude",
                "火山Ark",
                json.dumps(
                    {
                        "env": {
                            "ANTHROPIC_BASE_URL": "https://ark.cn-beijing.volces.com/api/coding",
                            "ANTHROPIC_AUTH_TOKEN": "ark-secret",  # 必须被忽略
                            "ANTHROPIC_DEFAULT_SONNET_MODEL": "MiniMax-M3",
                            "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "MiniMax-M3",  # 同模型去重
                            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "Kimi-K2.7-Code",
                            "ANTHROPIC_MODEL": "deepseek-v4-flash",
                        },
                        "model": "fable",
                    }
                ),
                1,
                0,
            )
        ],
    )
    monkeypatch.setattr(ms, "CCSWITCH_DB", str(db))
    rows = ms.ccswitch_matrix()["claude"]
    assert len(rows) == 1
    r = rows[0]
    assert r.provider == "火山Ark" and r.is_current is True
    assert r.base_url == "https://ark.cn-beijing.volces.com/api/coding"
    assert r.models == ["fable", "MiniMax-M3", "Kimi-K2.7-Code", "deepseek-v4-flash"]  # 去重、保序
    assert not any("secret" in m or "ark-" in m for m in r.models)


def test_ccswitch_matrix_codex_toml_and_catalog(tmp_path, monkeypatch):
    db = tmp_path / "cc-switch.db"
    _mk_db(
        db,
        [
            (
                "c1",
                "codex",
                "Kimi For Coding",
                json.dumps(
                    {
                        "config": 'model_provider = "custom"\nmodel = "kimi-for-coding"\n\n'
                        '[model_providers.custom]\nname = "kimi_coding"\n'
                        'base_url = "https://api.kimi.com/coding/v1"\nwire_api = "responses"\n',
                        "modelCatalog": {"models": [{"model": "kimi-for-coding"}, {"model": "k2.7-code"}]},
                    }
                ),
                0,
                1,
            )
        ],
    )
    monkeypatch.setattr(ms, "CCSWITCH_DB", str(db))
    rows = ms.ccswitch_matrix()["codex"]
    r = rows[0]
    assert r.models == ["kimi-for-coding", "k2.7-code"]  # config.model + modelCatalog,去重
    assert r.base_url == "https://api.kimi.com/coding/v1"


def test_ccswitch_matrix_missing_or_corrupt_db_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "CCSWITCH_DB", str(tmp_path / "nope.db"))
    assert ms.ccswitch_matrix() == {}
    bad = tmp_path / "bad.db"
    bad.write_text("not a sqlite file")
    monkeypatch.setattr(ms, "CCSWITCH_DB", str(bad))
    assert ms.ccswitch_matrix() == {}


def test_ccswitch_matrix_ignores_other_app_types(tmp_path, monkeypatch):
    db = tmp_path / "cc-switch.db"
    _mk_db(db, [("g1", "gemini", "Google", "{}", 1, 0), ("h1", "hermes", "x", "{}", 0, 1)])
    monkeypatch.setattr(ms, "CCSWITCH_DB", str(db))
    assert ms.ccswitch_matrix() == {}
