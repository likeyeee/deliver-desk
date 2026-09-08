from .config import MatchConfig
from .models import Job, salary_range


def reject_reason(job: Job, config: MatchConfig, *, detail: bool = False) -> str | None:
    title = job.title.casefold()
    if config.title_any and not any(word.casefold() in title for word in config.title_any):
        return "职位名称不匹配"
    if any(word.casefold() in job.company.casefold() for word in config.exclude_companies):
        return "公司在排除列表"
    content = " ".join([job.title, job.company, job.tags, job.description]).casefold()
    if any(word.casefold() in content for word in config.exclude):
        return "命中排除关键词"
    if detail and any(
        word.casefold() not in job.description.casefold() for word in config.description_all
    ):
        return "职位描述缺少必需关键词"
    if config.salary_min is not None or config.salary_max is not None:
        salary = salary_range(job.salary)
        if salary is None and config.unknown_salary == "skip" and detail:
            return "月薪无法可靠读取"
        if salary is not None:
            low, high = salary
            if config.salary_min is not None and high < config.salary_min:
                return "薪资区间低于要求"
            if config.salary_max is not None and low > config.salary_max:
                return "薪资区间高于要求"
    return None
