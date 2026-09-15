"""端到端冒烟测试：用 Flask 测试客户端走完整探究流程。"""
import json
import os
import tempfile

import pytest

import app as flask_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setattr(flask_app, "DB_PATH", db)
    flask_app.init_db()
    flask_app.app.config.update(TESTING=True)
    with flask_app.app.test_client() as c:
        yield c


def design():
    return {
        "name": "光照对绿豆发芽的影响",
        "hypothesis": "如果增加光照，那么发芽更快。",
        "repeats": 3,
        "factors": [
            {"name": "光照时间", "kind": "independent", "unit": "",
             "levels": ["0h", "4h", "8h"]},
            {"name": "发芽长度", "kind": "dependent", "unit": "mm", "levels": []},
            {"name": "温度", "kind": "controlled", "unit": "", "levels": ["25℃"]},
            {"name": "水量", "kind": "unassigned", "unit": "", "levels": []},
        ],
        "materials": [
            {"name": "绿豆", "per_round": 10, "stock": 80, "unit": "粒"},
        ],
        "slots": [
            {"label": "周三下午", "capacity": 6},
            {"label": "周五下午", "capacity": 6},
        ],
    }


def test_full_flow(client):
    # 创建
    r = client.post("/api/inquiries", json={"name": "测试"})
    assert r.status_code == 201
    iid = r.get_json()["inquiry"]["id"]

    # 保存设计
    s = client.put(f"/api/inquiries/{iid}", json=design()).get_json()
    codes = {i["code"] for i in s["issues"]}
    assert "W_UNCLASSIFIED" in codes           # 水量未归类
    assert "W_NO_IV" not in codes

    # 排演：3 水平 × 3 重复 = 9 轮，每组合恰好 3 次
    s = client.post(f"/api/inquiries/{iid}/schedule", json={}).get_json()
    assert len(s["rounds"]) == 9
    counts = {}
    for rnd in s["rounds"]:
        key = rnd["combo"]["光照时间"]
        counts[key] = counts.get(key, 0) + 1
    assert counts == {"0h": 3, "4h": 3, "8h": 3}
    # 9 轮分到两个时段
    slots_used = {rnd["slot_id"] for rnd in s["rounds"]}
    assert len(slots_used) == 2
    # 材料 10×9=90 > 库存 80，应报警
    assert any(i["code"] == "W_MATERIAL" for i in s["issues"])
    assert s["inquiry"]["generation"] == 1

    # 固定种子可复现：记录的顺序与重新排（generation 不前进时种子不同，
    # 因此直接验证回放一致）
    rep = client.get(f"/api/inquiries/{iid}/replay").get_json()
    assert rep["ok"] is True
    assert all(d["match"] for d in rep["details"])

    # 录入前 3 轮测量并锁定
    dv = next(f for f in s["factors"] if f["kind"] == "dependent")
    rounds = s["rounds"]
    values = [12.0, 13.5, None]
    for rnd, v in zip(rounds[:3], values):
        client.put(f"/api/inquiries/{iid}/rounds/{rnd['id']}/measurement", json={
            "dv_id": dv["id"], "dv_name": dv["name"], "value": v,
            "unit": "mm", "anomaly": "" if v is not None else "种子坏了",
        })
        client.post(f"/api/inquiries/{iid}/rounds/{rnd['id']}/lock", json={})

    # 第 2 轮单位混用
    client.put(f"/api/inquiries/{iid}/rounds/{rounds[1]['id']}/measurement", json={
        "dv_id": dv["id"], "dv_name": dv["name"], "value": 1.35,
        "unit": "cm", "anomaly": "",
    })
    s = client.get(f"/api/inquiries/{iid}").get_json()
    codes = {i["code"] for i in s["issues"]}
    assert "W_UNIT_MIX" in codes
    assert "W_MISSING" in codes
    miss = next(i for i in s["issues"] if i["code"] == "W_MISSING")
    assert miss["round_seq"] == 3

    # 剔除第 1 轮异常值
    client.put(f"/api/inquiries/{iid}/rounds/{rounds[0]['id']}/measurement", json={
        "dv_id": dv["id"], "dv_name": dv["name"], "value": 99,
        "unit": "mm", "anomaly": "尺子滑了", "excluded": True,
    })
    s = client.get(f"/api/inquiries/{iid}").get_json()
    assert any(i["code"] == "W_EXCLUDED" for i in s["issues"])

    # 统计：均值/极差
    st = s["stats"][0]
    assert st["dv_name"] == "发芽长度"
    grouped = {l["level"]: l for l in st["levels"]}
    # 第 1 轮被剔除，不应出现在任何水平里
    used_levels = {rnd["combo"]["光照时间"] for rnd in rounds[:3]}
    for lv_name in used_levels:
        vals = grouped.get(lv_name)
        if vals:
            assert 99 not in vals["values"]

    # 条件变化后只重排未完成：锁定轮必须保留在原位
    locked_before = [(r["seq"], r["combo"]) for r in s["rounds"] if r["locked"]]
    d = design()
    d["factors"][0]["levels"] = ["0h", "4h", "8h", "12h"]  # 加一个水平
    d["repeats"] = 2
    client.put(f"/api/inquiries/{iid}", json=d)
    s = client.post(f"/api/inquiries/{iid}/schedule", json={}).get_json()
    locked_after = [(r["seq"], r["combo"]) for r in s["rounds"] if r["locked"]]
    assert locked_before == locked_after
    # 锁定轮的测量没有丢
    kept = client.get(f"/api/inquiries/{iid}").get_json()
    assert len(kept["measurements"]) >= 3

    # 冻结
    r = client.post(f"/api/inquiries/{iid}/freeze", json={"label": "v1"})
    assert r.status_code == 200
    # 冻结后修改被拒
    r = client.put(f"/api/inquiries/{iid}", json=design())
    assert r.status_code == 409
    r = client.post(f"/api/inquiries/{iid}/schedule", json={})
    assert r.status_code == 409
    snap_id = r = client.get(f"/api/inquiries/{iid}").get_json()["snapshots"][0]["id"]
    snap = client.get(f"/api/inquiries/{iid}/snapshots/{snap_id}").get_json()
    assert snap["label"] == "v1"
    assert snap["state"]["inquiry"]["status"] == "frozen"


def test_schedule_balance_and_determinism():
    from scheduler import build_combos, schedule
    ivs = [{"name": "A", "ord": 0, "levels": ["a1", "a2"]},
           {"name": "B", "ord": 1, "levels": ["b1", "b2"]}]
    r1 = schedule(ivs, 4, seed=42, slots=[{"id": 1, "capacity": 20, "ord": 0}])
    r2 = schedule(ivs, 4, seed=42, slots=[{"id": 1, "capacity": 20, "ord": 0}])
    assert [r["combo"] for r in r1["rounds"]] == [r["combo"] for r in r2["rounds"]]
    assert len(r1["rounds"]) == 16
    # 每个组合 4 次
    keys = [json.dumps(r["combo"], sort_keys=True, ensure_ascii=False) for r in r1["rounds"]]
    assert all(keys.count(k) == 4 for k in set(keys))
    # 区组内每个组合恰好一次
    blocks = {i: set() for i in range(1, 5)}
    for r in r1["rounds"]:
        blocks[r["block_no"]].add(json.dumps(r["combo"], sort_keys=True))
    assert all(len(b) == 4 for b in blocks.values())
    assert len(build_combos(ivs)) == 4


def test_resource_conflict():
    from checks import check_design
    state = {
        "factors": [
            {"id": 1, "name": "x", "kind": "independent", "levels": ["h", "l"]},
            {"id": 2, "name": "y", "kind": "dependent", "unit": "s", "levels": []},
            {"id": 3, "name": "z", "kind": "controlled", "levels": ["20"]},
        ],
        "materials": [], "slots": [], "repeats": 1,
        "rounds": [
            {"id": 1, "seq": 1, "combo": {"x": "h"}, "slot_id": None, "locked": False, "done": False},
            {"id": 2, "seq": 2, "combo": {"x": "l"}, "slot_id": None, "locked": False, "done": False},
        ],
        "measurements": [],
    }
    codes = {i["code"] for i in check_design(state)}
    assert "W_SLOT_SHORT" in codes
