from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RequiredMaterial:
    """A material item requested from a candidate."""

    code: str
    name: str
    description: str
    upload_required: bool = True


COMMON_UPLOAD_MATERIALS: tuple[RequiredMaterial, ...] = (
    RequiredMaterial("badge_photo", "工作证照片", "优先上传，用于提前印制工作证。"),
    RequiredMaterial("id_card", "身份证件", "身份证或通行证正反面彩色扫描，清晰、1:1、正反面同页。"),
    RequiredMaterial("resume", "求职简历", "上传最新版求职简历，用于自动提取教育经历、工作经历、职称、资格和获奖信息。"),
    RequiredMaterial("degree_certificate", "学历、学位证书", "求职简历中的求学经历均需提供对应证书，毕业证和学位证分开扫描。"),
    RequiredMaterial("award_certificate", "职称证书、获奖证明", "简历中列明的职称、资格、获奖等需提供扫描件。"),
    RequiredMaterial("hukou_material", "户籍材料", "户口簿首页、个人页和登记事项变更页；如有户口迁移证可替代。"),
    RequiredMaterial("bank_card", "工资卡", "本人境内工商银行卡，建议在文件中写明姓名和完整卡号。"),
    RequiredMaterial("offer_letter", "聘用意向书", "扫描并上传 offer 全部内容，多页请合并。"),
    RequiredMaterial("securities_statement", "本人证券账户说明", "提供本人证券账户销户证明或中登系统未开立证明。"),
    RequiredMaterial("medical_report", "体检报告", "多页合并为 PDF 上传，前期已审核通过的无需重复准备。"),
    RequiredMaterial("profile_photo_intro", "新员工风采照片和简介", "准备清晰个人照片和约 200 字个人介绍。"),
)

CAMPUS_DOMESTIC_EXTRA: tuple[RequiredMaterial, ...] = (
    RequiredMaterial("employment_agreement", "就业协议、就业推荐表", "境内校招需分别扫描就业协议书和就业推荐表。"),
)

CAMPUS_OVERSEAS_EXTRA: tuple[RequiredMaterial, ...] = (
    RequiredMaterial("overseas_degree_certification", "留服认证", "境外学历需提供教育部留学服务中心国外学历学位认证书。"),
)

SOCIAL_EXTRA: tuple[RequiredMaterial, ...] = (
    RequiredMaterial("overseas_degree_certification", "留服认证", "有境外学历经历时提供；非留学学历无需准备。"),
    RequiredMaterial("resignation_certificate", "离职证明", "社招需上传离职证明，纸质版入职前寄送或入职当日递交原件。"),
    RequiredMaterial("non_compete_agreement", "竞业/保密/静默期协议", "如与前单位签署相关协议，上传至工作经历其他材料。"),
    RequiredMaterial("employment_manual", "就业创业证或劳动手册", "上海户籍且在上海缴交社保人员需提供。"),
)

FOLLOW_UP_ITEMS: tuple[RequiredMaterial, ...] = (
    RequiredMaterial("fund_qualification", "岗位资格资格说明", "已通过考试的入职后办理注册，未通过的按 HR 通知备考。", False),
    RequiredMaterial("party_youth_transfer", "党/团组织关系转移", "党员、预备党员或团员按指引准备材料并在入职后办理。", False),
    RequiredMaterial("personnel_file", "人事档案说明", "公司不接收人事档案，请自行安排存档。", False),
    RequiredMaterial("social_security", "社保、公积金、企业年金说明", "社保、公积金和企业年金按员工类型及城市规则办理。", False),
    RequiredMaterial("self_media_declaration", "自媒体账号申报", "继续使用个人自媒体账号的，入职后按制度申报。", False),
)


def normalize_employment_type(employment_type: str | None) -> str:
    """Normalize free-form employment type text to supported catalog buckets."""

    value = (employment_type or "").strip().lower()
    if any(keyword in value for keyword in ("社招", "社会", "social")):
        return "social"
    if "境外" in value or "海外" in value or "overseas" in value:
        return "campus_overseas"
    if "校招" in value or "应届" in value or "校园" in value or "campus" in value or "境内" in value:
        return "campus_domestic"
    return "campus_domestic"


def required_materials_for_employment_type(employment_type: str | None) -> list[RequiredMaterial]:
    """Return the required materials based on the candidate employment type."""

    bucket = normalize_employment_type(employment_type)
    materials = list(COMMON_UPLOAD_MATERIALS)
    if bucket == "social":
        materials.extend(SOCIAL_EXTRA)
    elif bucket == "campus_overseas":
        materials.extend(CAMPUS_OVERSEAS_EXTRA)
    else:
        materials.extend(CAMPUS_DOMESTIC_EXTRA)
    materials.extend(FOLLOW_UP_ITEMS)
    return materials


def format_materials_message(employment_type: str | None = None) -> str:
    """Build a concise candidate-facing material checklist message."""

    bucket_names = {
        "campus_domestic": "校招-境内",
        "campus_overseas": "校招-境外",
        "social": "社招",
    }
    bucket = normalize_employment_type(employment_type)
    upload_items = [item for item in required_materials_for_employment_type(employment_type) if item.upload_required]
    follow_up_items = [item for item in required_materials_for_employment_type(employment_type) if not item.upload_required]

    lines = [
        f"已按“{bucket_names[bucket]}”口径生成材料清单：",
        "需上传至预入职系统：",
    ]
    lines.extend(f"- {item.name}：{item.description}" for item in upload_items)
    lines.append("入职后关注事项：")
    lines.extend(f"- {item.name}：{item.description}" for item in follow_up_items)
    lines.append("提交前请点击保存；全部材料确认无误后再提交审核。")
    return "\n".join(lines)
