"""可解释活动目录与规划输出协议，不依赖服务/调度器。"""
from typing import Literal
from pydantic import BaseModel, Field
from backend.world import J3, J4, ZX2

CATALOG = {
    'research': dict(title='推进科研与项目', rooms=[f'{J3}/R03', f'{J3}/R05', f'{J4}/R03'], weights={'student': 4, 'advisor': 3}),
    'library': dict(title='图书分馆查阅资料', rooms=[f'{ZX2}/zx2_lib'], weights={'student': 3, 'advisor': 2, 'staff': 1}),
    'class': dict(title='课程学习与教学交流', rooms=[f'{ZX2}/{r}' for r in ('zx2_n219','zx2_n215','zx2_n256','zx2_n250','zx2_n246','zx2_s205','zx2_s207','zx2_s202','zx2_s218','zx2_s230')], weights={'student': 2, 'advisor': 1, 'staff': 0.5}),
    'exercise': dict(title='健身休息与调整', rooms=[f'{ZX2}/zx2_gym'], weights={'student': 1, 'advisor': 0.5, 'staff': 0.5}),
    'seminar': dict(title='组会与学术交流', rooms=[f'{J4}/R05'], weights={'student': 1, 'advisor': 3, 'staff': 0.5}),
    'admin': dict(title='整理教学与行政资料', rooms=[f'{J4}/R02', f'{J3}/R08'], weights={'staff': 6, 'advisor': 0.5}),
    'break': dict(title='茶水间短暂休息', rooms=[f'{J3}/R07', f'{J4}/R07'], weights={'student': 1, 'advisor': 1, 'staff': 1}),
    'visit': dict(title='约同学讨论项目', rooms=[f'{ZX2}/zx2_lib', f'{J3}/R07', f'{J4}/R07'], weights={'student': 0.7, 'advisor': 0.5}),
}


class PlannedActivity(BaseModel):
    kind: Literal['research','library','class','exercise','seminar','admin','break','visit']
    area: str
    start_minute: int = Field(ge=0, le=1439)
    duration: float = Field(default=180, ge=30, le=360, allow_inf_nan=False)
    basis: Literal['routine','memory','event','relationship'] = 'routine'
    reason: str = Field(default='',max_length=160)
    partner: str = ''


class DailyPlan(BaseModel):
    activities: list[PlannedActivity] = Field(min_length=1, max_length=3)
