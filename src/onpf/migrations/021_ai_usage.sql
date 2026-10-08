CREATE TABLE ai_usage (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    program_id TEXT NOT NULL REFERENCES programs(id),
    started_at TEXT NOT NULL,
    day TEXT NOT NULL,
    state TEXT NOT NULL,
    reserved_micro INTEGER NOT NULL,
    charged_micro INTEGER NOT NULL,
    input_price INTEGER,
    output_price INTEGER,
    usage_json TEXT,
    finished_at TEXT
);
CREATE INDEX ai_usage_day ON ai_usage(day,user_id,program_id);
