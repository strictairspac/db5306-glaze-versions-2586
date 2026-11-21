from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class LoginForm(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=128)


class CaseForm(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any]


class ReviewForm(BaseModel):
    note: str = Field(min_length=1, max_length=500)


# ---------------------------------------------------------------------------
# 离线改方模块
# ---------------------------------------------------------------------------
class VersionForm(BaseModel):
    operation: Literal['写入', '删除']
    recipe: str | None = None

    @model_validator(mode='after')
    def check_recipe(self):
        if self.operation == '写入':
            if not isinstance(self.recipe, str) or not self.recipe.strip():
                raise ValueError('写入操作的釉料配方须为非空文本')
            self.recipe = self.recipe.strip()
        else:
            if self.recipe is not None and self.recipe.strip():
                raise ValueError('删除操作的釉料配方须留空')
            self.recipe = None
        return self


class SnapshotForm(BaseModel):
    snapshot_code: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=120)


class OrderItemForm(BaseModel):
    # 领单时固化的基线：base_version_no 与 base_recipe 都来自领单当时的现行版本。
    formula_id: str = Field(min_length=1, max_length=64)
    base_version_no: int = Field(ge=0)          # 0 表示领单时该配方尚不存在
    base_recipe: str | None = None              # 基线为删除/不存在时为空
    operation: Literal['写入', '删除']
    recipe: str | None = None

    @model_validator(mode='after')
    def check_recipe(self):
        if self.operation == '写入':
            if not isinstance(self.recipe, str) or not self.recipe.strip():
                raise ValueError(f'配方 {self.formula_id} 的写入内容须为非空文本')
            self.recipe = self.recipe.strip()
        else:
            if self.recipe is not None and self.recipe.strip():
                raise ValueError(f'配方 {self.formula_id} 是删除操作，配方须留空')
            self.recipe = None
        self.formula_id = self.formula_id.strip()
        return self


class ChangeOrderForm(BaseModel):
    order_id: str = Field(min_length=8, max_length=36)
    title: str = Field(min_length=1, max_length=120)
    items: list[OrderItemForm] = Field(min_length=1)

    @field_validator('order_id')
    @classmethod
    def check_order_id(cls, value):
        value = value.strip()
        # 接收 UUID 或同类幂等键；禁止空白与路径语义字符。
        if any(ch in value for ch in ' /\\?#'):
            raise ValueError('批单编号格式不合法')
        return value

    @model_validator(mode='after')
    def check_unique_formula(self):
        ids = [item.formula_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError('同一批单内配方编号不能重复')
        return self


class RetryForm(BaseModel):
    # 中断重试时回传单号；服务端只认首次收单结果，绝不重复生效。
    order_id: str = Field(min_length=8, max_length=36)
