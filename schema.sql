-- 变量控制排演板 · SQLite 结构
PRAGMA foreign_keys = ON;

-- 探究主题
CREATE TABLE IF NOT EXISTS inquiry (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    hypothesis  TEXT NOT NULL DEFAULT '',
    seed        INTEGER NOT NULL DEFAULT 20260915, -- 固定随机种子
    generation  INTEGER NOT NULL DEFAULT 0,        -- 每次重排 +1
    repeats     INTEGER NOT NULL DEFAULT 3,        -- 每个水平组合的重复次数
    status      TEXT NOT NULL DEFAULT 'draft',     -- draft | frozen
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    frozen_at   TEXT
);

-- 因素卡片：independent 自变量 / dependent 因变量 / controlled 控制变量 / unassigned 未归类
CREATE TABLE IF NOT EXISTS factor (
    id          INTEGER PRIMARY KEY,
    inquiry_id  INTEGER NOT NULL REFERENCES inquiry(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'unassigned',
    unit        TEXT NOT NULL DEFAULT '',  -- 因变量的计量单位
    levels      TEXT,                      -- JSON 数组：自变量的水平；控制变量为 [固定值]
    ord         INTEGER NOT NULL DEFAULT 0
);

-- 材料份数
CREATE TABLE IF NOT EXISTS material (
    id          INTEGER PRIMARY KEY,
    inquiry_id  INTEGER NOT NULL REFERENCES inquiry(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    per_round   REAL NOT NULL DEFAULT 1,   -- 每轮消耗份数
    stock       REAL NOT NULL DEFAULT 0,   -- 可用份数
    unit        TEXT NOT NULL DEFAULT '',
    ord         INTEGER NOT NULL DEFAULT 0
);

-- 可用时段（资源），capacity 为该时段最多可同时容纳的轮次数
CREATE TABLE IF NOT EXISTS slot (
    id          INTEGER PRIMARY KEY,
    inquiry_id  INTEGER NOT NULL REFERENCES inquiry(id) ON DELETE CASCADE,
    label       TEXT NOT NULL,
    capacity    INTEGER NOT NULL DEFAULT 1,
    ord         INTEGER NOT NULL DEFAULT 0
);

-- 一轮实验：一个水平组合
CREATE TABLE IF NOT EXISTS round (
    id          INTEGER PRIMARY KEY,
    inquiry_id  INTEGER NOT NULL REFERENCES inquiry(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,          -- 从 1 开始的次序
    combo       TEXT NOT NULL,             -- JSON：{自变量名: 水平}
    slot_id     INTEGER REFERENCES slot(id) ON DELETE SET NULL,
    block_no    INTEGER NOT NULL DEFAULT 0,-- 区组号（每区组含全部组合各一次）
    locked      INTEGER NOT NULL DEFAULT 0,-- 已完成并锁定：重排时保留
    done        INTEGER NOT NULL DEFAULT 0,
    UNIQUE(inquiry_id, seq)
);

-- 一轮中某个因变量的一次测量
CREATE TABLE IF NOT EXISTS measurement (
    id          INTEGER PRIMARY KEY,
    round_id    INTEGER NOT NULL REFERENCES round(id) ON DELETE CASCADE,
    dv_id       INTEGER REFERENCES factor(id) ON DELETE SET NULL,
    dv_name     TEXT NOT NULL,
    value       REAL,                      -- NULL 表示缺测
    unit        TEXT NOT NULL DEFAULT '',
    anomaly     TEXT NOT NULL DEFAULT '',  -- 异常原因
    excluded    INTEGER NOT NULL DEFAULT 0 -- 直接剔除（删除异常值）
);

-- 随机依据：最近一次排演的种子、输入参数与全部 RNG 事件
CREATE TABLE IF NOT EXISTS rng_log (
    inquiry_id  INTEGER PRIMARY KEY REFERENCES inquiry(id) ON DELETE CASCADE,
    seed_used   INTEGER NOT NULL,
    params      TEXT NOT NULL DEFAULT '{}',
    events      TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 冻结版本快照
CREATE TABLE IF NOT EXISTS snapshot (
    id          INTEGER PRIMARY KEY,
    inquiry_id  INTEGER NOT NULL REFERENCES inquiry(id) ON DELETE CASCADE,
    label       TEXT NOT NULL,
    state       TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
