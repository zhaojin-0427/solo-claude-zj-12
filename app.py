"""变量控制排演板 · Flask 后端

SQLite 持久化，负责：探究版本存取、平衡随机排演、检查、统计与冻结快照。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory

import scheduler as sched
import checks as checker

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "inquiry.db"
SCHEMA = BASE / "schema.sql"

app = Flask(__name__, static_folder=str(BASE / "static"), static_url_path="/static")


# ---------------------------------------------------------------- 数据库 ----
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- 序列化 ----
def _loads(raw, default):
    if not raw:
        return default
    return json.loads(raw)


def serialize_inquiry(db, inquiry_id: int) -> dict | None:
    row = db.execute("SELECT * FROM inquiry WHERE id=?", (inquiry_id,)).fetchone()
    if not row:
        return None
    inq = dict(row)

    factors = [dict(r) for r in db.execute(
        "SELECT * FROM factor WHERE inquiry_id=? ORDER BY ord, id", (inquiry_id,))]
    for f in factors:
        f["levels"] = _loads(f.pop("levels"), [])

    materials = [dict(r) for r in db.execute(
        "SELECT * FROM material WHERE inquiry_id=? ORDER BY ord, id", (inquiry_id,))]
    slots = [dict(r) for r in db.execute(
        "SELECT * FROM slot WHERE inquiry_id=? ORDER BY ord, id", (inquiry_id,))]

    rounds = [dict(r) for r in db.execute(
        "SELECT * FROM round WHERE inquiry_id=? ORDER BY seq", (inquiry_id,))]
    for r in rounds:
        r["combo"] = _loads(r["combo"], {})
        r["locked"] = bool(r["locked"])
        r["done"] = bool(r["done"])

    mrows = db.execute(
        """SELECT m.* FROM measurement m JOIN round r ON m.round_id=r.id
           WHERE r.inquiry_id=?""", (inquiry_id,)).fetchall()
    measurements = []
    for m in mrows:
        d = dict(m)
        d["excluded"] = bool(d["excluded"])
        d["round_seq"] = next((r["seq"] for r in rounds if r["id"] == d["round_id"]), None)
        measurements.append(d)

    log = db.execute("SELECT * FROM rng_log WHERE inquiry_id=?", (inquiry_id,)).fetchone()
    rng_log = dict(log) if log else None
    if rng_log:
        rng_log["events"] = _loads(rng_log["events"], [])
        rng_log["params"] = _loads(rng_log["params"], {})

    snapshots = [dict(r) for r in db.execute(
        "SELECT id,label,created_at FROM snapshot WHERE inquiry_id=? ORDER BY id",
        (inquiry_id,))]

    state = {
        "inquiry": {k: inq[k] for k in
                    ("id", "name", "hypothesis", "seed", "generation",
                     "repeats", "status", "created_at", "updated_at", "frozen_at")},
        "factors": factors,
        "materials": materials,
        "slots": slots,
        "rounds": rounds,
        "measurements": measurements,
        "rng_log": rng_log,
        "snapshots": snapshots,
    }
    state["issues"] = checker.check_design(state) + checker.check_data(state)
    state["stats"] = compute_stats(state)
    return state


def compute_stats(state: dict) -> list[dict]:
    """各因变量 × 每个自变量各水平：原始数据、均值、极差。

    返回 [{"dv_id","dv_name","unit","by_iv":[{"iv_id","iv_name","levels":[...]}]}]。
    有两个自变量时，两个自变量都会各自出一组统计（分别折叠另一因素的轮次）。
    """
    ivs = [f for f in state["factors"] if f["kind"] == "independent"]
    dv_factors = [f for f in state["factors"] if f["kind"] == "dependent"]
    rounds_by_id = {r["id"]: r for r in state["rounds"]}

    # groups[dv_id][iv_id][水平] = [测量值]
    groups: dict = {dv["id"]: {iv["id"]: {} for iv in ivs} for dv in dv_factors}
    for m in state["measurements"]:
        if m.get("excluded") or m.get("value") is None:
            continue
        rnd = rounds_by_id.get(m["round_id"])
        if not rnd or m.get("dv_id") not in groups:
            continue
        for iv in ivs:
            level = str(rnd["combo"].get(iv["name"], ""))
            groups[m["dv_id"]][iv["id"]].setdefault(level, []).append(float(m["value"]))

    def summarize(vals: list[float]) -> dict:
        return {
            "values": vals,
            "mean": round(sum(vals) / len(vals), 4) if vals else None,
            "range": round(max(vals) - min(vals), 4) if vals else None,
            "min": min(vals) if vals else None,
            "max": max(vals) if vals else None,
            "n": len(vals),
        }

    out = []
    for dv in dv_factors:
        by_iv = []
        for iv in ivs:
            order = {str(v): i for i, v in enumerate(iv.get("levels") or [])}
            per_level = [
                {"level": level, **summarize(vals)}
                for level, vals in groups[dv["id"]][iv["id"]].items()
            ]
            per_level.sort(key=lambda x: order.get(x["level"], 999))
            by_iv.append({"iv_id": iv["id"], "iv_name": iv["name"], "levels": per_level})
        out.append({"dv_id": dv["id"], "dv_name": dv["name"],
                    "unit": dv.get("unit", ""), "by_iv": by_iv})
    return out


# ------------------------------------------------------------- 通用工具 ----
def _get_or_404(db, inquiry_id: int):
    row = db.execute("SELECT * FROM inquiry WHERE id=?", (inquiry_id,)).fetchone()
    if not row:
        return None, (jsonify({"error": "探究不存在"}), 404)
    return row, None


def _guard_frozen(row):
    if row["status"] == "frozen":
        return jsonify({"error": "版本已冻结，不能修改；请创建新探究或解冻复制。"}), 409
    return None


def _touch(db, inquiry_id: int):
    db.execute("UPDATE inquiry SET updated_at=datetime('now','localtime') WHERE id=?",
               (inquiry_id,))


def _replace_children(db, inquiry_id: int, payload: dict):
    """整体替换因素 / 材料 / 时段（设计保存时以前端状态为准）。"""
    keep_factor_ids = set()
    for i, f in enumerate(payload.get("factors", [])):
        levels = f.get("levels") or ([f.get("fixed")] if f.get("fixed") else [])
        levels_json = json.dumps(levels, ensure_ascii=False)
        if f.get("id"):
            db.execute(
                """UPDATE factor SET name=?, kind=?, unit=?, levels=?, ord=?
                   WHERE id=? AND inquiry_id=?""",
                (f["name"], f["kind"], f.get("unit", ""), levels_json, i,
                 f["id"], inquiry_id))
            keep_factor_ids.add(f["id"])
        else:
            cur = db.execute(
                """INSERT INTO factor(inquiry_id,name,kind,unit,levels,ord)
                   VALUES(?,?,?,?,?,?)""",
                (inquiry_id, f["name"], f["kind"], f.get("unit", ""), levels_json, i))
            keep_factor_ids.add(cur.lastrowid)
    if "factors" in payload:
        db.execute("DELETE FROM factor WHERE inquiry_id=? AND id NOT IN (%s)"
                   % (",".join(str(i) for i in keep_factor_ids) or "0"),
                   (inquiry_id,))

    keep_mat = set()
    for i, m in enumerate(payload.get("materials", [])):
        if m.get("id"):
            db.execute(
                """UPDATE material SET name=?, per_round=?, stock=?, unit=?, ord=?
                   WHERE id=? AND inquiry_id=?""",
                (m["name"], m.get("per_round", 1), m.get("stock", 0),
                 m.get("unit", ""), i, m["id"], inquiry_id))
            keep_mat.add(m["id"])
        else:
            cur = db.execute(
                """INSERT INTO material(inquiry_id,name,per_round,stock,unit,ord)
                   VALUES(?,?,?,?,?,?)""",
                (inquiry_id, m["name"], m.get("per_round", 1),
                 m.get("stock", 0), m.get("unit", ""), i))
            keep_mat.add(cur.lastrowid)
    if "materials" in payload:
        db.execute("DELETE FROM material WHERE inquiry_id=? AND id NOT IN (%s)"
                   % (",".join(str(i) for i in keep_mat) or "0"), (inquiry_id,))

    keep_slot = set()
    for i, s in enumerate(payload.get("slots", [])):
        if s.get("id"):
            db.execute(
                "UPDATE slot SET label=?, capacity=?, ord=? WHERE id=? AND inquiry_id=?",
                (s["label"], max(1, int(s.get("capacity", 1))), i, s["id"], inquiry_id))
            keep_slot.add(s["id"])
        else:
            cur = db.execute(
                "INSERT INTO slot(inquiry_id,label,capacity,ord) VALUES(?,?,?,?)",
                (inquiry_id, s["label"], max(1, int(s.get("capacity", 1))), i))
            keep_slot.add(cur.lastrowid)
    if "slots" in payload:
        db.execute("DELETE FROM slot WHERE inquiry_id=? AND id NOT IN (%s)"
                   % (",".join(str(i) for i in keep_slot) or "0"), (inquiry_id,))


# ------------------------------------------------------------------ 页面 ----
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ----------------------------------------------------------- 探究 CRUD ----
@app.get("/api/inquiries")
def list_inquiries():
    db = get_db()
    rows = db.execute(
        """SELECT id,name,status,generation,updated_at FROM inquiry ORDER BY id DESC"""
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/inquiries")
def create_inquiry():
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    cur = db.execute(
        "INSERT INTO inquiry(name,hypothesis,seed,repeats) VALUES(?,?,?,?)",
        (data.get("name", "未命名探究"), data.get("hypothesis", ""),
         int(data.get("seed", 20260915)), max(1, int(data.get("repeats", 3)))))
    iid = cur.lastrowid
    db.commit()
    return jsonify(serialize_inquiry(db, iid)), 201


@app.get("/api/inquiries/<int:iid>")
def get_inquiry(iid):
    db = get_db()
    state = serialize_inquiry(db, iid)
    if not state:
        return jsonify({"error": "探究不存在"}), 404
    return jsonify(state)


@app.put("/api/inquiries/<int:iid>")
def update_inquiry(iid):
    db = get_db()
    row, err = _get_or_404(db, iid)
    if err:
        return err
    frozen_err = _guard_frozen(row)
    if frozen_err:
        return frozen_err

    data = request.get_json(force=True, silent=True) or {}
    db.execute(
        "UPDATE inquiry SET name=?, hypothesis=?, repeats=?, seed=? WHERE id=?",
        (data.get("name", row["name"]), data.get("hypothesis", row["hypothesis"]),
         max(1, int(data.get("repeats", row["repeats"]))),
         int(data.get("seed", row["seed"])), iid))
    _replace_children(db, iid, data)
    _touch(db, iid)
    db.commit()
    return jsonify(serialize_inquiry(db, iid))


@app.delete("/api/inquiries/<int:iid>")
def delete_inquiry(iid):
    db = get_db()
    _, err = _get_or_404(db, iid)
    if err:
        return err
    db.execute("DELETE FROM inquiry WHERE id=?", (iid,))
    db.commit()
    return jsonify({"ok": True})


# ------------------------------------------------------------ 排演引擎 ----
@app.post("/api/inquiries/<int:iid>/schedule")
def run_schedule(iid):
    db = get_db()
    row, err = _get_or_404(db, iid)
    if err:
        return err
    frozen_err = _guard_frozen(row)
    if frozen_err:
        return frozen_err

    data = request.get_json(force=True, silent=True) or {}
    reschedule_all = bool(data.get("reschedule_all", False))

    factors = [dict(r) for r in db.execute(
        "SELECT * FROM factor WHERE inquiry_id=? ORDER BY ord,id", (iid,))]
    for f in factors:
        f["levels"] = _loads(f["levels"], [])
    ivs = [f for f in factors if f["kind"] == "independent"]
    slots = [dict(r) for r in db.execute(
        "SELECT * FROM slot WHERE inquiry_id=? ORDER BY ord,id", (iid,))]

    generation = row["generation"] + 1
    # 第 1 代排演直接使用填写的种子；之后每重排一代 +1，
    # 这样既保证「填什么种子第一轮就是什么种子」，又能让条件变化后排法不同但仍可复现。
    seed_used = int(row["seed"]) + max(0, generation - 1) \
        if data.get("advance_seed", True) else int(row["seed"])

    locked_rounds = []
    if not reschedule_all:
        locked_rounds = [dict(r) for r in db.execute(
            "SELECT seq,combo FROM round WHERE inquiry_id=? AND locked=1 ORDER BY seq",
            (iid,))]

    result = sched.schedule(ivs, row["repeats"], seed_used, slots, locked_rounds)

    # 重写轮次表：锁定轮的 done/测量需保留 —— 按 seq 映射旧轮。
    # 注意：DELETE 会因外键级联删掉 measurement，必须先取出旧轮映射，
    # 插入新轮后立即把测量重新挂到新 id 上。
    old_rows = db.execute("SELECT * FROM round WHERE inquiry_id=?", (iid,)).fetchall()
    old = {r["seq"]: dict(r) for r in old_rows}
    old_measurements: dict[int, list[dict]] = {}
    for r in old_rows:
        old_measurements[r["id"]] = [dict(m) for m in db.execute(
            "SELECT * FROM measurement WHERE round_id=?", (r["id"],))]
    db.execute("DELETE FROM round WHERE inquiry_id=?", (iid,))
    for r in result["rounds"]:
        prev = old.get(r["seq"]) if r["locked"] else None
        cur = db.execute(
            """INSERT INTO round(inquiry_id,seq,combo,slot_id,block_no,locked,done)
               VALUES(?,?,?,?,?,?,?)""",
            (iid, r["seq"], json.dumps(r["combo"], ensure_ascii=False),
             r.get("slot_id"), r.get("block_no", 0),
             1 if (prev or r["locked"]) else 0,
             1 if prev and prev["done"] else 0))
        if prev:
            # 把旧轮测量迁移到新轮（旧轮已随 DELETE 被级联删除，此处重新插入）
            for m in old_measurements.get(prev["id"], []):
                db.execute(
                    """INSERT INTO measurement(round_id,dv_id,dv_name,value,unit,anomaly,excluded)
                       VALUES(?,?,?,?,?,?,?)""",
                    (cur.lastrowid, m["dv_id"], m["dv_name"], m["value"],
                     m["unit"], m["anomaly"], m["excluded"]))

    db.execute(
        """INSERT INTO rng_log(inquiry_id,seed_used,params,events,created_at)
           VALUES(?,?,?,?,datetime('now','localtime'))
           ON CONFLICT(inquiry_id) DO UPDATE SET
             seed_used=excluded.seed_used, params=excluded.params,
             events=excluded.events, created_at=excluded.created_at""",
        (iid, seed_used,
         json.dumps(result["params"], ensure_ascii=False),
         json.dumps(result["events"], ensure_ascii=False)))
    db.execute("UPDATE inquiry SET generation=? WHERE id=?", (generation, iid))
    _touch(db, iid)
    db.commit()
    return jsonify(serialize_inquiry(db, iid))


@app.post("/api/inquiries/<int:iid>/rounds/<int:rid>/lock")
def lock_round(iid, rid):
    db = get_db()
    row, err = _get_or_404(db, iid)
    if err:
        return err
    frozen_err = _guard_frozen(row)
    if frozen_err:
        return frozen_err
    data = request.get_json(force=True, silent=True) or {}
    locked = 0 if data.get("locked") is False else 1
    db.execute("UPDATE round SET locked=?, done=? WHERE id=? AND inquiry_id=?",
               (locked, locked, rid, iid))
    _touch(db, iid)
    db.commit()
    return jsonify(serialize_inquiry(db, iid))


# ------------------------------------------------------------- 测量录入 ----
@app.put("/api/inquiries/<int:iid>/rounds/<int:rid>/measurement")
def put_measurement(iid, rid):
    db = get_db()
    row, err = _get_or_404(db, iid)
    if err:
        return err
    frozen_err = _guard_frozen(row)
    if frozen_err:
        return frozen_err
    data = request.get_json(force=True, silent=True) or {}
    dv_id = data.get("dv_id")
    value = data.get("value")
    try:
        value = float(value) if value not in (None, "", "null") else None
    except (TypeError, ValueError):
        return jsonify({"error": "测量值必须是数字或留空（缺测）"}), 400

    existing = db.execute(
        "SELECT id FROM measurement WHERE round_id=? AND IFNULL(dv_id,-1)=IFNULL(?,-1)",
        (rid, dv_id)).fetchone()
    if existing:
        db.execute(
            """UPDATE measurement SET value=?, unit=?, anomaly=?, excluded=?, dv_name=?
               WHERE id=?""",
            (value, data.get("unit", ""), data.get("anomaly", ""),
             1 if data.get("excluded") else 0, data.get("dv_name", ""), existing["id"]))
    else:
        db.execute(
            """INSERT INTO measurement(round_id,dv_id,dv_name,value,unit,anomaly,excluded)
               VALUES(?,?,?,?,?,?,?)""",
            (rid, dv_id, data.get("dv_name", ""), value,
             data.get("unit", ""), data.get("anomaly", ""),
             1 if data.get("excluded") else 0))
    _touch(db, iid)
    db.commit()
    return jsonify(serialize_inquiry(db, iid))


# ------------------------------------------------------- 回放 / 冻结 ----
@app.get("/api/inquiries/<int:iid>/replay")
def replay(iid):
    db = get_db()
    log = db.execute("SELECT * FROM rng_log WHERE inquiry_id=?", (iid,)).fetchone()
    if not log:
        return jsonify({"error": "还没有排演记录，先点击「排出实验次序」。"}), 404
    events = _loads(log["events"], [])
    params = _loads(log["params"], {})
    result = sched.replay(events, params)
    result["stored_events"] = events
    result["created_at"] = log["created_at"]
    return jsonify(result)


@app.post("/api/inquiries/<int:iid>/freeze")
def freeze(iid):
    db = get_db()
    row, err = _get_or_404(db, iid)
    if err:
        return err
    data = request.get_json(force=True, silent=True) or {}
    label = data.get("label") or f"冻结版本 {datetime.now():%Y-%m-%d %H:%M}"
    db.execute("UPDATE inquiry SET status='frozen', frozen_at=datetime('now','localtime') WHERE id=?",
               (iid,))
    db.commit()
    state = serialize_inquiry(db, iid)
    db.execute("INSERT INTO snapshot(inquiry_id,label,state) VALUES(?,?,?)",
               (iid, label, json.dumps(state, ensure_ascii=False)))
    _touch(db, iid)
    db.commit()
    return jsonify(serialize_inquiry(db, iid))


@app.get("/api/inquiries/<int:iid>/snapshots/<int:sid>")
def get_snapshot(iid, sid):
    db = get_db()
    row = db.execute(
        "SELECT * FROM snapshot WHERE id=? AND inquiry_id=?", (sid, iid)).fetchone()
    if not row:
        return jsonify({"error": "快照不存在"}), 404
    payload = {"id": row["id"], "label": row["label"], "created_at": row["created_at"],
               "state": _loads(row["state"], {})}
    return jsonify(payload)


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
