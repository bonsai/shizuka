# shizuka 2.0 全体模式図

```mermaid
flowchart TB
    subgraph CLI["CLI 群"]
        O[opencode]
        Q[qwen]
        C[cline]
        K[kilo]
        G[gemini]
        CR[crush]
        CL[claude]
    end

    subgraph MCP["MCP サーバ (Python)"]
        M[mcp_server.py]
        R[recommend.py]
        U[usage.py]
        QT[quota.py]
    end

    subgraph DB["ローカル DB"]
        D[(models.db)]
        D_t[providers<br/>cli_priority<br/>usage_log<br/>rotation_state<br/>recovery_estimate<br/>ml_params]
    end

    subgraph BQ["BigQuery"]
        BQD[(model_status)]
        BQM[BQML<br/>recovery_model<br/>recommend_model]
    end

    subgraph SYNC["同期パイプライン"]
        BS[bq_sync.py]
    end

    %% 接続
    CLI -.-> |mm_log_usage: 使用量記録| M
    CLI -.-> |mm_set: モデル切替| M

    M --> |read/write| D
    M --> |mm_recommend: 推奨| R
    M --> |mm_usage: 使用量| U
    M --> |mm_quota: 残量確認| QT

    D --> |usage_log 定期同期| BS
    BS --> |INSERT| BQD

    BQD --> BQM
    BQM --> |recovery_estimate / 推奨| BQD
    BQD --> |SELECT| BS
    BS --> |sync back| D

    style M fill:#4a9,stroke:#333
    style D fill:#f93,stroke:#333
    style BQD fill:#69f,stroke:#333
    style BQM fill:#96f,stroke:#333
```

## データフロー

```
CLI → mm_log_usage → MCP → models.db
                                ↓ bq_sync.py
                           BigQuery (model_status)
                                ↓ BQML
                           recovery_estimate / 推奨
                                ↓ bq_sync.py
                           models.db (反映)
```

## 各レイヤーの責務

| レイヤー | 役割 | 技術 |
|----------|------|------|
| **CLI 群** | モデル設定読み書き・使用量ログ送信 | 各CLIの設定ファイル |
| **MCP サーバ** | CLI操作の窓口・DB CRUD・推奨・使用量グラフ・Quota確認 | Python (mcp_server.py) |
| **models.db** | ローカル状態保持（providers / usage_log / rotation_state / ml_params） | SQLite (WAL) |
| **bq_sync.py** | usage_log → BQ 同期 + BQML結果 ← BQ 反映 | bq CLI / REST API |
| **BigQuery** | 長期間保存・BQML 推論（復帰予測・モデル推奨） | BQML (Linear regression) |
| **BQML** | recovery_estimate・recommend_model の訓練と推論 | CREATE MODEL / ML.PREDICT |
```
