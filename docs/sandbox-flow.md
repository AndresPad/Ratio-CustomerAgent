# Sandbox Data Processing Flow

```mermaid
sequenceDiagram
    participant MCP as MCP Server
    participant RA as Reasoning Agent
    participant SA as Sandbox Agent
    participant SB as Sandbox Container<br/>(/mnt/data/)

    MCP->>RA: Raw data (JSON rows)
    RA->>RA: Data size check

    alt Small enough
        RA->>RA: Use raw data directly
    else Too large
        RA->>SA: Delegate large data processing
        SA->>SB: upload_file_to_sandbox(folder, filename, data)
        SB-->>SA: File written: /{folder}/{filename}
        SA->>SB: execute_python_in_sandbox(reduction script)
        Note over SB: Read → Reduce →<br/>Aggregate / Filter
        SB->>SB: Save processed output:<br/>/{folder}/processed_{filename}
        SB-->>SA: Confirmation + processed file path
        SA-->>RA: File reference (replaces raw data)
    end

    RA->>RA: Continue planning

    opt Agent needs processed data
        RA->>SA: Request processed data
        SA->>SB: download_sandbox_file(remote_path)
        SB-->>SA: File content returned
        SA-->>RA: Processed data
    end

    RA->>RA: Use data for response / analysis
```

## Tool Sequence

| Step | Tool | Purpose |
|------|------|---------|
| 1 | — | MCP returns raw data (JSON rows) |
| 2 | `upload_file_to_sandbox` | Push large raw data into sandbox `/mnt/data/` |
| 3 | `execute_python_in_sandbox` | Run reduction script that reads uploaded file, processes it, saves output |
| 4 | — | Agent receives file reference instead of raw data |
| 5 | `download_sandbox_file` | Agent fetches processed data when needed for reasoning |
