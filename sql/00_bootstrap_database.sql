IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'raw')
BEGIN
    EXEC('CREATE SCHEMA raw');
END;

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'hrm')
BEGIN
    EXEC('CREATE SCHEMA hrm');
END;

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'stg')
BEGIN
    EXEC('CREATE SCHEMA stg');
END;

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'zen')
BEGIN
    EXEC('CREATE SCHEMA zen');
END;

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'audit')
BEGIN
    EXEC('CREATE SCHEMA audit');
END;

IF OBJECT_ID('audit.PipelineRunLog', 'U') IS NOT NULL
    DROP TABLE audit.PipelineRunLog;

CREATE TABLE audit.PipelineRunLog (
    PipelineRunID INT IDENTITY(1,1) PRIMARY KEY,
    RunID VARCHAR(50) NOT NULL,
    PipelineName VARCHAR(100) NOT NULL,
    StartTime DATETIME2 NOT NULL,
    EndTime DATETIME2 NULL,
    DurationMs INT NULL,
    Status VARCHAR(20) NOT NULL,
    SourceTable VARCHAR(200) NULL,
    TargetTable VARCHAR(200) NULL,
    SourceRowCount INT NULL,
    TargetRowCount INT NULL,
    ErrorMessage VARCHAR(4000) NULL
);

IF OBJECT_ID('audit.TableRowCountLog', 'U') IS NOT NULL
    DROP TABLE audit.TableRowCountLog;

CREATE TABLE audit.TableRowCountLog (
    TableRowCountLogID INT IDENTITY(1,1) PRIMARY KEY,
    RunID VARCHAR(50) NOT NULL,
    PipelineName VARCHAR(100) NOT NULL,
    TableName VARCHAR(200) NOT NULL,
    SourceFile VARCHAR(4000) NULL,
    SourceFolderPath VARCHAR(4000) NULL,
    CountStage VARCHAR(50) NOT NULL,
    ProperRowCount INT NOT NULL,
    MalformedRowCount INT NOT NULL,
    CapturedAt DATETIME2 NOT NULL
);

IF OBJECT_ID('audit.ColumnProfileLog', 'U') IS NOT NULL
    DROP TABLE audit.ColumnProfileLog;

CREATE TABLE audit.ColumnProfileLog (
    RunID VARCHAR(50) NOT NULL,
    PipelineName VARCHAR(100) NOT NULL,
    TableName VARCHAR(200) NOT NULL,
    SourceFile VARCHAR(4000) NULL,
    SourceFolderPath VARCHAR(4000) NULL,
    CountStage VARCHAR(50) NOT NULL,
    OrdinalPosition INT NULL,
    ColumnName VARCHAR(200) NOT NULL,
    MinimumNonNullValue NVARCHAR(4000) NULL,
    MaximumNonNullValue NVARCHAR(4000) NULL,
    MaxStringLength INT NULL,
    BlankRowCount INT NOT NULL,
    NonNullRowCount INT NOT NULL,
    NullableFlag BIT NOT NULL,
    Nullability VARCHAR(10) NOT NULL,
    RecommendedSqlType VARCHAR(100) NULL,
    DefinedSqlType VARCHAR(100) NOT NULL,
    MinMaxValueTypeMismatchFlag BIT NOT NULL
        CONSTRAINT DF_ColumnProfileLog_MinMaxValueTypeMismatchFlag DEFAULT (0),
    CapturedAt DATETIME2 NOT NULL,
    AutoId BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY
);

CREATE UNIQUE INDEX UX_audit_ColumnProfileLog_AutoId
    ON audit.ColumnProfileLog (AutoId);

IF OBJECT_ID('audit.LoadExecutionLog', 'U') IS NOT NULL
    DROP TABLE audit.LoadExecutionLog;

CREATE TABLE audit.LoadExecutionLog (
    LoadExecutionLogID INT IDENTITY(1,1) PRIMARY KEY,
    RunID VARCHAR(50) NULL,
    PipelineName VARCHAR(100) NULL,
    TableName VARCHAR(200) NOT NULL,
    SourceFile VARCHAR(4000) NULL,
    LoadMethod VARCHAR(50) NOT NULL,
    LoadStatus VARCHAR(50) NOT NULL,
    ChunkRowCount INT NOT NULL,
    StartedAt DATETIME2 NOT NULL,
    FinishedAt DATETIME2 NOT NULL,
    DurationMs INT NOT NULL,
    ServerName VARCHAR(255) NULL,
    ExecutablePath VARCHAR(4000) NULL,
    ExitCode INT NULL,
    DetailMessage NVARCHAR(4000) NULL,
    BcpErrorRowCount INT NULL,
    BcpErrorSummary NVARCHAR(4000) NULL,
    StdOut NVARCHAR(4000) NULL,
    StdErr NVARCHAR(4000) NULL
);
