from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field, PrivateAttr, ValidationInfo, field_validator, model_validator

from .normalization import disclosed_text, has_parallel_numbers, lot_identity, model_match_key, money_to_yuan, number_value, review_status


class ExtractionModel(BaseModel):
    field_conflicts: dict[str, list[Any]] = Field(default_factory=dict)
    _normalization_warnings: list[str] = PrivateAttr(default_factory=list)
    _numeric_fields: ClassVar[dict[str, str]] = {}
    _numeric_subject: ClassVar[tuple[str, str]] = ('记录', '')

    @model_validator(mode='wrap')
    @classmethod
    def flag_parallel_numbers(cls, values, handler):
        record = handler(values)
        if isinstance(values, dict):
            label, name_field = cls._numeric_subject
            name = getattr(record, name_field, '') or '未命名'
            for field, field_label in cls._numeric_fields.items():
                if has_parallel_numbers(values.get(field)):
                    message = f'{label}“{name}”的{field_label}含分号并列数值，已置空而非取第一段，需拆分核验：{str(values[field])[:240]}'
                    if message not in record._normalization_warnings:
                        record._normalization_warnings.append(message)
        return record.collect_numeric_warnings()

    def collect_numeric_warnings(self):
        return self

    @model_validator(mode='before')
    @classmethod
    def normalize_missing_values(cls, values):
        if not isinstance(values, dict):
            return values
        values = dict(values)
        for name, field in cls.model_fields.items():
            if name in values and values[name] is None:
                if field.annotation is str:
                    values[name] = ''
                elif field.default_factory in (list, dict):
                    values[name] = field.default_factory()
        return values


class ItemExtract(ExtractionModel):
    _numeric_fields = {'unit_price': '单价', 'quantity': '数量', 'total_price': '总价'}
    _numeric_subject = ('标的', 'product_name')
    lot_name: str = ""
    line_id: str = ""
    product_name: str = ""
    category: str = ""
    brand: str = ""
    model: str = ""
    model_key: str = ""  # Exact-match key; never replaces the source model text.
    unit_price: float | None = None
    quantity: float | None = None
    total_price: float | None = None
    supplier: str = ""
    source: str = "announcement"
    evidence: str = ""
    confidence: float = 0.0
    source_file: str = ""
    source_location: str = ""
    quantity_unit: str = ""
    derived_values: dict[str, float] = Field(default_factory=dict)
    business_line_id: str = ""  # Source business code; never a unique row key by itself.
    related_sources: list[dict[str, str]] = Field(default_factory=list)
    source_context: dict[str, str] = Field(default_factory=dict)
    aggregation_eligible: bool = True
    aggregation_reason: str = ""
    # Filled from verified source cells, never accepted as model proof.
    field_observations: dict[str, dict[str, str]] = Field(default_factory=dict)

    @field_validator('product_name', 'category', 'brand', 'model', mode='before')
    @classmethod
    def actual_value(cls, value, info: ValidationInfo):
        return disclosed_text(value, info.field_name)

    @model_validator(mode='after')
    def align_model_key(self):
        self.model_key = model_match_key(self.model)
        return self

    def model_dump(self, **kwargs):
        excluded = kwargs.pop('exclude', None)
        excluded = set(excluded or ())
        excluded.add('model_key')
        return super().model_dump(exclude=excluded, **kwargs)

    @field_validator("unit_price", "total_price", mode="before")
    @classmethod
    def money(cls, value):
        return money_to_yuan(value)

    @field_validator("quantity", mode="before")
    @classmethod
    def quantity_number(cls, value):
        return number_value(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def clamp_confidence(cls, value: float) -> float:
        return max(0.0, min(1.0, number_value(value) or 0))


class BidExtract(ExtractionModel):
    _numeric_fields = {'bid_amount': '报价', 'score': '得分'}
    _numeric_subject = ('投标人', 'bidder')
    bidder: str
    lot_name: str = ""
    is_winner: bool = False
    bid_amount: float | None = None
    score: float | None = None
    rank: int | None = None
    evidence: str = ""
    review_status: str = "未披露"
    qualification_status: str = "未披露"
    compliance_status: str = "未披露"
    review_reason: str = ""
    non_winning_reason: str = ""
    consortium_id: str = ""
    source_file: str = ""
    related_sources: list[dict[str, str]] = Field(default_factory=list)
    identity_confirmed: bool = True
    evaluation_scores: list[float | dict[str, Any]] = Field(default_factory=list)

    @field_validator('evaluation_scores', mode='before')
    @classmethod
    def normalize_score_evidence(cls, values):
        if values is None:
            return []
        if not isinstance(values, list):
            raise ValueError('evaluation_scores must be an array')
        scores = []
        for value in values:
            if isinstance(value, dict):
                # Providers also return labelled component scores. Keep their
                # labels so a technical score is not mistaken for a total.
                key = next((key for key in ('value', 'score') if key in value), None)
                number = number_value(value[key]) if key else None
                if number is not None:
                    scores.append({**value, key: number})
            else:
                number = number_value(value)
                if number is not None:
                    scores.append(number)
        return scores

    @model_validator(mode='before')
    @classmethod
    def preserve_review_reason(cls, values):
        if isinstance(values,dict) and not values.get('review_reason'):
            explanations = [str(values[field]) for field in ('review_status','qualification_status','compliance_status')
                if isinstance(values.get(field),str) and review_status(values[field])=='未通过' and values[field]!='未通过']
            if explanations:
                values = {**values,'review_reason':'；'.join(explanations)}
        return values

    @field_validator("bid_amount", mode="before")
    @classmethod
    def money(cls, value):
        return money_to_yuan(value)

    @field_validator("score", mode="before")
    @classmethod
    def score_number(cls, value):
        return number_value(value)

    @field_validator("review_status", "qualification_status", "compliance_status", mode="before")
    @classmethod
    def normalize_review(cls, value):
        return review_status(value)

    @property
    def eligible(self) -> bool:
        from .bid_identity import non_company_name_reason
        if not self.identity_confirmed or non_company_name_reason(self.bidder):
            return False
        states = (self.review_status, self.qualification_status, self.compliance_status)
        if "未通过" in states:
            return False
        return self.is_winner or self.review_status == "通过" or (self.qualification_status == self.compliance_status == "通过") or bool(self.evaluation_scores)

    @property
    def role(self) -> str:
        from .bid_identity import non_company_name_reason
        if not self.identity_confirmed or non_company_name_reason(self.bidder):
            return '其他'
        if "未通过" in (self.review_status, self.qualification_status, self.compliance_status):
            return "其他"
        return "中标人" if self.is_winner else "参与人"


class LotExtract(ExtractionModel):
    _numeric_fields = {'budget_amount': '预算金额', 'awarded_amount': '中标金额'}
    _numeric_subject = ('采购包', 'lot_name')
    lot_name: str = ""
    lot_key: str = ""  # Internal identity; never replaces the source label in competition sheets.
    budget_amount: float | None = None
    awarded_amount: float | None = None
    items: list[ItemExtract] = Field(default_factory=list)
    bids: list[BidExtract] = Field(default_factory=list)

    @field_validator("budget_amount", "awarded_amount", mode="before")
    @classmethod
    def money(cls, value):
        return money_to_yuan(value)


class ProjectExtract(ExtractionModel):
    _numeric_fields = {'budget_amount': '预算金额', 'awarded_amount': '中标金额'}
    _numeric_subject = ('项目', 'project_name')
    notice_uuid: str = ""
    source_url: str = ""
    lot_name: str = ""
    project_name: str = ""
    project_code: str = ""
    purchaser: str = ""
    budget_amount: float | None = None
    awarded_amount: float | None = None
    announcement_date: str = ""
    items: list[ItemExtract] = Field(default_factory=list)
    bids: list[BidExtract] = Field(default_factory=list)
    extraction_method: str = "deepseek"
    warnings: list[str] = Field(default_factory=list)
    lots: list[LotExtract] = Field(default_factory=list)
    processing_status: str = "complete"
    no_target_reason: str = ""
    input_hash: str = ""
    model_usage: dict[str, Any] = Field(default_factory=dict)

    def collect_numeric_warnings(self):
        records = [self, *self.items, *self.bids, *self.lots,
                   *(item for lot in self.lots for item in lot.items),
                   *(bid for lot in self.lots for bid in lot.bids)]
        warnings = [warning for record in records for warning in record._normalization_warnings]
        if any(record.field_conflicts for record in records):
            warnings.append('存在未确认的金额候选值，相关金额留空并保留原文供核验')
        if warnings:
            self.warnings = list(dict.fromkeys([*self.warnings, *warnings]))
            if self.processing_status == 'complete':
                self.processing_status = 'partial'
        return self

    @field_validator("budget_amount", "awarded_amount", mode="before")
    @classmethod
    def money(cls, value):
        return money_to_yuan(value)

    def package_records(self) -> list[LotExtract]:
        if self.lots:
            return self.lots
        names = list(dict.fromkeys([x.lot_name for x in [*self.items, *self.bids] if x.lot_name]))
        if not names:
            names = [self.lot_name]
        records = []
        for name in names:
            records.append(LotExtract(lot_name=name,
                budget_amount=self.budget_amount if len(names) == 1 else None,
                awarded_amount=self.awarded_amount if len(names) == 1 else None,
                items=[x for x in self.items if x.lot_name == name or (not x.lot_name and len(names) == 1)],
                bids=[x for x in self.bids if x.lot_name == name or (not x.lot_name and len(names) == 1)]))
        unassigned_items = [x for x in self.items if not x.lot_name] if len(names) > 1 else []
        unassigned_bids = [x for x in self.bids if not x.lot_name] if len(names) > 1 else []
        if unassigned_items or unassigned_bids:
            records.append(LotExtract(items=unassigned_items, bids=unassigned_bids))
        return records

    def assign_lot_keys(self) -> list[LotExtract]:
        lots = self.package_records()
        for index, lot in enumerate(lots, 1):
            lot.lot_key = lot_identity(lot.lot_name) or f'unassigned:{index}'
        self.lots = lots
        return lots


class JobState(BaseModel):
    id: str
    status: str
    total: int = 0
    completed: int = 0
    succeeded: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)
    dataset_id: str = "default"
    mode: str = "extract"
    partial: int = 0
    skipped: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    elapsed_seconds: float | None = None
    finished: bool = False
    metrics: dict[str, Any] = Field(default_factory=dict)


class LocalIngestRequest(BaseModel):
    dataset_id: str = Field("official", min_length=1, max_length=80, pattern=r"^[\w-]+$")
    limit: int | None = Field(None, ge=1, le=2000)
    notice_ids: list[str] = Field(default_factory=list, max_length=2000)
    announcement_only: bool = False
    mode: str = Field("extract", pattern="^(extract|parse-only|replay)$")


class ApiResult(BaseModel):
    data: Any

