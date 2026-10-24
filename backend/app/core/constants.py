"""全系统枚举与常量（与架构规格书 / Agent 契约书保持一致）。"""
from enum import StrEnum


class AssetType(StrEnum):
    """资产类型（ArtifactRepo，契约书·publish_artifact 枚举）。"""

    PROFILE = "profile"                # 探查报告
    METRIC_SPEC = "metric_spec"        # 指标口径
    SQL = "sql"                        # 可执行 SQL
    DATASET = "dataset"                # 原始数据集
    DQ_REPORT = "dq_report"            # 质检报告
    FEATURE_SET = "feature_set"        # 特征集 / 特征字典
    CHART = "chart"                    # 图表
    INSIGHT = "insight"                # 洞察分析（结构化发现 + 证据）
    REPORT = "report"                  # 分析报告
    REVIEW = "review"                  # 评审意见书
    RETROSPECTIVE = "retrospective"    # 复盘纪要
    TRACE = "trace"                    # 溯源报告


class AssetStatus(StrEnum):
    """资产状态（artifact.status，03-数据模型 DDL 注释）。"""

    PUBLISHED = "published"
    BLOCKED = "blocked"
    IN_USE = "in_use"
    ARCHIVED = "archived"


class VersionStatus(StrEnum):
    """资产版本状态（artifact_version.status）。"""

    ACTIVE = "active"


class ReviewStatus(StrEnum):
    """评审状态（artifact_version.review_status）。"""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class NodeStatus(StrEnum):
    """DAG 节点状态机（TaskRepo）。"""

    PENDING = "PENDING"
    READY = "READY"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    PLANNING = "PLANNING"  # Planner 拆解中（持租约，v2 调度状态）
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class Role(StrEnum):
    """一期三档角色。"""

    ADMIN = "admin"
    ANALYST = "analyst"
    READONLY = "readonly"


class Severity(StrEnum):
    """数据质量定级。"""

    MINOR = "minor"          # 轻微
    MAJOR = "major"          # 严重
    BLOCKING = "blocking"    # 阻断


class ExperienceType(StrEnum):
    """记忆经验类型（submit_experience_summary）。"""

    PITFALL = "pitfall"
    OPTIMIZATION = "optimization"
    TEMPLATE = "template"
    SUCCESS_PATTERN = "success_pattern"


# Agent 运行时默认（单一来源：agent_service._default_config 与 base._load_config 共用，
# 保证“UI 配置面板显示 = 实际运行时强制”，避免默认无超时导致 LLM 节点挂死拖住 worker）
AGENT_DEFAULT_TIMEOUT_SECONDS = 120  # 单节点执行超时（asyncio.wait_for）
AGENT_DEFAULT_MAX_RETRIES = 2        # 单节点总尝试次数

# Agent 能力标签（Agent 契约书·十二章注册表；insight_analysis 为 v3 新增分析层）
AGENT_CAPABILITY: dict[str, str] = {
    "planner": "orchestrator",
    "metric_definition": "metric_definition",
    "data_profiler": "metadata_profiling",
    "sql_agent": "sql_extract",
    "data_quality": "quality_check",
    "feature_engineer": "feature_engineer",
    "visual_agent": "visualization",
    "insight_analyst": "insight_analysis",
    "report_scribe": "report_writing",
    "analyst_review": "review",
    "memory_keeper": "memory_management",
    "trace_navigator": "trace",
}

# Agent 展示名（大屏/列表，键 = AGENT_CAPABILITY 的键）· 拟人化名称_职称
AGENT_DISPLAY_NAMES: dict[str, str] = {
    "planner": "天枢_调度",
    "metric_definition": "素问_指标定义",
    "data_profiler": "观澜_数据探查",
    "sql_agent": "灵枢_SQL抽取",
    "data_quality": "明鉴_数据质检",
    "feature_engineer": "炼金_特征工程",
    "visual_agent": "丹青_可视化",
    "insight_analyst": "天机_洞察分析",
    "report_scribe": "文澜_报告编撰",
    "analyst_review": "洛璃_终审",
    "memory_keeper": "忆安_记忆管理",
    "trace_navigator": "闻道_溯源",
}

# 企业数据脱敏规则（敏感字段自动脱敏，见 infra/datasource.py mask_sensitive）
# 规则 key 为字段名匹配词，值为正则；命中字段名则对该列值脱敏。
SENSITIVE_FIELD_PATTERNS: dict[str, str] = {
    "phone": r"(手机|电话|mobile|phone|tel)",
    "id_card": r"(身份证|id_card|idcard|证件)",
    "email": r"(邮箱|email|mail)",
}
MASK_RULES: dict[str, str] = {
    "phone": r"(?<!\d)(1[3-9]\d{9})(?!\d)",
    "id_card": r"\d{17}[\dXx]",
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+",
}
