from datetime import date, datetime
from typing import Literal, Annotated
from pydantic import BaseModel, ConfigDict, Field, model_validator, StringConstraints


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)


class Login(Strict):
    username: str = Field(min_length=1, max_length=80)
    password: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(min_length=1, max_length=256)
    otp: str = Field(default='', max_length=8)


class UserCreate(Strict):
    username: str = Field(pattern=r'^[a-zA-Z0-9_.-]{3,80}$')
    name: str = Field(min_length=1, max_length=120)
    password: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(min_length=14, max_length=256)
    admin: bool = False


class CaseData(Strict):
    case_no: str = Field(min_length=1, max_length=80)
    examination_date: date
    requesting_authority: str = Field(min_length=1, max_length=200)
    examiner: str = Field(min_length=1, max_length=120)
    identification: Literal['identified', 'unidentified', 'provisional'] = 'unidentified'
    subject_reference: str = Field(default='', max_length=200)
    priority: Literal['routine', 'urgent'] = 'routine'
    due_date: date | None = None
    notes: str = Field(default='', max_length=10000)


class CaseUpdate(CaseData):
    revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=1000)


class Member(Strict):
    user_id: str
    role: Literal['examiner', 'reviewer', 'coordinator', 'auditor']
    revision: int = Field(ge=1)


class RecordData(Strict):
    text: str = Field(default='', max_length=20000)
    region: str = Field(default='', max_length=160)
    laterality: Literal['', 'left', 'right', 'midline', 'bilateral', 'not_applicable'] = ''
    number: int | None = Field(default=None, ge=1, le=9999)
    length_mm: float | None = Field(default=None, ge=0, le=100000)
    width_mm: float | None = Field(default=None, ge=0, le=100000)
    depth_mm: float | None = Field(default=None, ge=0, le=100000)
    weight_g: float | None = Field(default=None, ge=0, le=100000)
    volume_ml: float | None = Field(default=None, ge=0, le=100000)
    container: str = Field(default='', max_length=200)
    preservative: str = Field(default='', max_length=200)
    seal: str = Field(default='', max_length=200)
    custodian: str = Field(default='', max_length=160)
    specimen_id: str | None = None
    evidence_id: str | None = None
    status: Literal['pending', 'received', 'reviewed', 'complete', 'cancelled'] = 'pending'
    assignee: str = Field(default='', max_length=120)
    due_date: date | None = None


class RecordCreate(Strict):
    revision: int = Field(ge=1)
    kind: Literal['external', 'internal', 'organ', 'injury', 'specimen', 'lab', 'task']
    label: str = Field(min_length=1, max_length=160)
    data: RecordData
    reason: str = Field(default='Initial entry', min_length=3, max_length=1000)

    @model_validator(mode='after')
    def injury_requires_number(self):
        if self.kind == 'injury' and self.data.number is None:
            raise ValueError('A numbered injury record is required')
        return self


class RecordUpdate(Strict):
    revision: int = Field(ge=1)
    data: RecordData
    reason: str = Field(min_length=3, max_length=1000)
    active: bool = True


class Revision(Strict):
    revision: int = Field(ge=1)


class RevokeMember(Revision):
    reason: str = Field(min_length=3, max_length=1000)


class CustodyCreate(Revision):
    specimen_id: str
    from_custodian: str = Field(min_length=1, max_length=160)
    to_custodian: str = Field(min_length=1, max_length=160)
    seal: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=3, max_length=2000)
    occurred_at: datetime

    @model_validator(mode='after')
    def aware_date(self):
        if self.occurred_at.tzinfo is None:
            raise ValueError('Include a UTC offset in the custody timestamp')
        return self


class TemplateSection(Strict):
    key: str = Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    title: str = Field(min_length=1, max_length=120)
    required: bool = False


class TemplateCreate(Strict):
    name: str = Field(min_length=1, max_length=120)
    sections: list[TemplateSection] = Field(min_length=1, max_length=30)

    @model_validator(mode='after')
    def unique_keys(self):
        if len({s.key for s in self.sections}) != len(self.sections):
            raise ValueError('Section keys must be unique')
        return self


class ReportCreate(Revision):
    template_id: str
    kind: Literal['initial', 'supplementary'] = 'initial'
    parent_id: str | None = None


class ReportSection(Strict):
    key: str = Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    title: str = Field(min_length=1, max_length=120)
    text: str = Field(max_length=50000)


class ReportUpdate(Strict):
    version: int = Field(ge=1)
    sections: list[ReportSection] = Field(min_length=1, max_length=30)


class ReportAction(Strict):
    version: int = Field(ge=1)
    acknowledgements: dict[str, str] = Field(default_factory=dict, max_length=200)


class CommentCreate(Strict):
    body: str = Field(min_length=3, max_length=5000)
    blocking: bool = False


class PasswordChange(Strict):
    old_password: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(min_length=1, max_length=256)
    new_password: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(min_length=14, max_length=256)
