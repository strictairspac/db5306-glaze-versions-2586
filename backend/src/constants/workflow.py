from enum import StrEnum


class Role(StrEnum):
    OPERATOR = 'operator'
    REVIEWER = 'reviewer'
    VIEWER = 'viewer'


class Status(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    COMPLETE = 'complete'
    FAILED = 'failed'


class OrderStatus(StrEnum):
    PENDING = 'pending'      # 待收
    ACCEPTED = 'accepted'    # 已接纳
    REJECTED = 'rejected'    # 已退回（存在冲突）


class ChangeOperation(StrEnum):
    UPSERT = '写入'
    DELETE = '删除'
