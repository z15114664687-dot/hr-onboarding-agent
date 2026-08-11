from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MaterialStandard:
    """Upload and content standard for one onboarding material type."""

    material_type: str
    display_name: str
    accepted_formats: tuple[str, ...]
    required_fields: tuple[str, ...]
    upload_rules: tuple[str, ...]
    candidate_feedback: str


COMMON_SCAN_RULES = (
    "使用原件彩色扫描或清晰电子原件，不使用翻拍、截图、压缩模糊文件。",
    "画面完整无遮挡、无反光、无裁切，文字和印章可辨认。",
    "多页材料合并为一个 PDF；单页图片建议使用 jpg/png。",
)


MATERIAL_STANDARDS: dict[str, MaterialStandard] = {
    "badge_photo": MaterialStandard(
        "badge_photo",
        "工作证照片",
        ("jpg", "jpeg", "png"),
        ("人像清晰", "照片大小"),
        (
            "建议深色西装配浅色衬衫，或深色衬衫；男士可配领带。",
            "可侧身或全正面，五官清晰、表情自然。",
            "图片大小应大于 200K。",
            "不接受墨镜照、自拍大头照、证件寸照、合影或背景人群明显的照片。",
        ),
        "请重新上传清晰个人工作证照片，建议着正式服装，图片大于 200K，避免自拍、墨镜照、合影和证件寸照。",
    ),
    "id_card": MaterialStandard(
        "id_card",
        "身份证件",
        ("jpg", "jpeg", "png", "pdf"),
        ("姓名", "身份证号", "有效期限"),
        (
            "身份证、港澳台通行证需正反面齐全。",
            "通过打印机彩色扫描，扫描比例 1:1，正反面放在同一页。",
            "护照仅需彩色扫描个人信息页。",
        )
        + COMMON_SCAN_RULES,
        "请重新上传身份证/证件正反面彩色扫描件，要求 1:1、正反面同页、清晰完整无遮挡。",
    ),
    "resume": MaterialStandard(
        "resume",
        "求职简历",
        ("pdf", "doc", "docx", "jpg", "jpeg", "png"),
        ("姓名", "求学经历"),
        (
            "上传最新版求职简历，需包含姓名、求学经历、工作经历或实习经历等核心信息。",
            "请提取简历中列明的教育经历、工作经历、职称/资格、获奖信息，供后续材料核对。",
            "简历中列明的学历、学位、职称、资格、获奖等，需要在对应材料项提交证明。",
            "建议上传 PDF 或 Word 原文件；图片需清晰完整，不要只上传局部截图。",
        ),
        "请重新上传最新版完整求职简历，确保姓名、教育经历、工作经历及列明的职称/资格/获奖信息清晰可识别。",
    ),
    "degree_certificate": MaterialStandard(
        "degree_certificate",
        "学历、学位证书",
        ("jpg", "jpeg", "png", "pdf"),
        ("姓名", "学校", "证书类型", "证书编号"),
        (
            "毕业证书和学位证书需从封套中取出后分别扫描，上传至对应位置。",
            "应提交学校发放的毕业证书和学位证书，不以学信网报告替代。",
            "同一学历阶段需同时包含毕业证书和学位证书；只上传其中一项需补充。",
            "求职简历中列明的求学经历均需提供对应证书。",
            "电子版毕业证书文件、手机拍照等不符合要求。",
        )
        + COMMON_SCAN_RULES,
        "请重新上传学校发放的毕业证书和学位证书原件彩色扫描件；两类证书需都包含，不能用学信网报告替代。",
    ),
    "xuexin_report": MaterialStandard(
        "xuexin_report",
        "学信网学历/学籍/学位报告",
        ("pdf", "jpg", "jpeg", "png"),
        ("姓名", "学校", "专业", "学历层次或学位类别", "证书编号或在线验证码", "报告有效期"),
        ("报告需完整展示在线验证码、报告有效期和个人学历/学位信息。",) + COMMON_SCAN_RULES,
        "请重新上传完整学信网报告，确保在线验证码、报告有效期、姓名、学校和专业信息清晰可见。",
    ),
    "overseas_degree_certification": MaterialStandard(
        "overseas_degree_certification",
        "教育部留学服务中心国外学历学位认证书",
        ("pdf", "jpg", "jpeg", "png"),
        ("姓名", "认证书编号", "院校", "国别/地区", "认证结论"),
        ("境外学历必须提供留服认证；若本硕博均为境外学历，各学历段均需提供。",) + COMMON_SCAN_RULES,
        "请重新上传完整留服认证书，确保认证书编号、院校、国别/地区和认证结论清晰可见。",
    ),
    "resignation_certificate": MaterialStandard(
        "resignation_certificate",
        "离职证明",
        ("pdf", "jpg", "jpeg", "png"),
        ("姓名", "原单位名称", "离职/解除/终止日期", "盖章识别结果"),
        (
            "纸质离职证明需彩色扫描后上传，原件需按 HR 要求寄送或入职当日递交。",
            "电子签章文件不可修改、转换或打印后扫描。",
            "如未注明工作起始日期，需重新出具或提供首份劳动合同/其他证明材料。",
        )
        + COMMON_SCAN_RULES,
        "请重新上传符合要求的离职证明，需包含姓名、原单位、离职日期和有效盖章；电子签章文件不要转换或打印后扫描。",
    ),
    "medical_report": MaterialStandard(
        "medical_report",
        "体检报告",
        ("pdf",),
        ("姓名", "体检日期", "体检机构", "总检结论"),
        ("多页体检报告需合并为一个 PDF。", "需包含总检结论页。") + COMMON_SCAN_RULES,
        "请重新上传完整体检报告 PDF，确保包含姓名、体检日期、体检机构和总检结论页。",
    ),
    "bank_card": MaterialStandard(
        "bank_card",
        "工资卡",
        ("pdf", "jpg", "jpeg", "png"),
        ("银行名称", "银行卡号"),
        (
            "需为本人境内工商银行卡，卡面或材料中应能识别“中国工商银行/ICBC”。",
            "银行卡通常不显示姓名，姓名不作为自动驳回或人工复核的必要条件。",
            "卡号需清晰完整；如出于安全需要遮挡，应至少能确认银行和卡号主体信息。",
        ),
        "请重新上传本人境内工商银行卡材料，确保能识别中国工商银行/ICBC 和完整卡号。",
    ),
    "hukou_material": MaterialStandard(
        "hukou_material",
        "户籍材料",
        ("pdf", "jpg", "jpeg", "png"),
        ("户口本首页", "个人常住人口登记卡页", "登记事项变更页"),
        ("户口簿首页不是户主页；如持有户口迁移证，可提供户口迁移证扫描件。",) + COMMON_SCAN_RULES,
        "请重新上传户口簿首页、个人页和登记事项变更页；注意户口簿首页不是户主页。",
    ),
    "securities_statement": MaterialStandard(
        "securities_statement",
        "证券账户证明",
        ("pdf", "jpg", "jpeg", "png"),
        ("姓名", "证件号", "账户状态或未开户证明"),
        (
            "本人证券账户需提供销户证明或中登系统未开立证明。",
            "截图/证明应完整、清晰展示个人身份信息和账户状态。",
        )
        + COMMON_SCAN_RULES,
        "请重新上传证券账户销户证明或未开立证明，确保姓名、证件号和账户状态完整清晰。",
    ),
    "labor_termination_form": MaterialStandard(
        "labor_termination_form",
        "退工单（上海）",
        ("pdf", "jpg", "jpeg", "png"),
        ("姓名", "原单位名称", "退工日期", "盖章识别结果"),
        COMMON_SCAN_RULES,
        "请重新上传清晰完整的退工单，确保姓名、原单位、退工日期和盖章可辨认。",
    ),
    "employment_manual": MaterialStandard(
        "employment_manual",
        "就业创业证/劳动手册（上海）",
        ("pdf",),
        ("封面", "个人照片页", "盖章页"),
        ("上海户籍且在上海缴交社保人员需提供；封面、个人照片页、所有盖章页需合并为一个 PDF。",)
        + COMMON_SCAN_RULES,
        "请重新上传就业创业证或劳动手册 PDF，需包含封面、个人照片页和所有盖章页。",
    ),
    "employment_agreement": MaterialStandard(
        "employment_agreement",
        "就业协议、就业推荐表",
        ("pdf", "jpg", "jpeg", "png"),
        ("姓名", "学校", "就业协议或推荐表"),
        ("就业协议书、就业推荐表需分开扫描上传。",) + COMMON_SCAN_RULES,
        "请重新上传就业协议书和就业推荐表，两个文件需分开扫描，个人和学校信息清晰可见。",
    ),
    "professional_certificate": MaterialStandard(
        "professional_certificate",
        "职称证书",
        ("pdf", "jpg", "jpeg", "png"),
        ("姓名", "证书名称", "证书编号或发证机构"),
        ("简历中列明的职称、资格等需提供证书/证明扫描件；多页文件合并 PDF。",) + COMMON_SCAN_RULES,
        "请重新上传清晰完整的职称/资格证书，确保姓名、证书名称和编号或发证机构可辨认。",
    ),
    "award_certificate": MaterialStandard(
        "award_certificate",
        "获奖证明",
        ("pdf", "jpg", "jpeg", "png"),
        ("姓名", "奖项名称", "发证机构或公章"),
        ("简历中列明的获奖证明需提供扫描件；需能识别获奖人、奖项名称、学校/机构或公章/落款；多页文件合并 PDF。",)
        + COMMON_SCAN_RULES,
        "请重新上传清晰完整的获奖证明，确保获奖人姓名、奖项名称、学校/机构或公章/落款可辨认。",
    ),
}


def get_material_standard(material_type: str) -> MaterialStandard | None:
    """Return upload standard by material type."""

    return MATERIAL_STANDARDS.get(material_type)


def material_standard_prompt(material_type: str) -> str:
    """Return concise standard text for OCR/LLM extraction prompt."""

    standard = get_material_standard(material_type)
    if not standard:
        return "暂无该材料的专门标准，请按清晰、完整、原件彩色扫描原则判断。"
    rules = "\n".join(f"- {rule}" for rule in standard.upload_rules)
    fields = "、".join(standard.required_fields)
    formats = "、".join(standard.accepted_formats)
    return (
        f"材料名称：{standard.display_name}\n"
        f"接受格式：{formats}\n"
        f"必看字段：{fields}\n"
        f"上传标准：\n{rules}"
    )


def material_visual_reference_prompt(material_type: str) -> str:
    """Return visual classification hints based on attachment-one examples."""

    references = {
        "badge_photo": (
            "附件1第1页为员工卡证件照示例：应是一名单人半身或头像照片，五官清晰、表情自然，"
            "建议正式服装；不要把身份证照、自拍大头照、合影、戴墨镜照片或明显生活照判为合格。"
        ),
        "id_card": "附件1第2-4页为身份证、港澳台通行证、护照示例；身份证需正反面齐全并同页。",
        "resume": (
            "简历不是附件1中的证明类图片，但它是后续材料核对依据；"
            "请判断上传内容是否为完整求职简历，并结构化提取教育经历、工作经历、职称/资格、获奖信息。"
        ),
        "degree_certificate": (
            "附件1第5-8页分别为硕士毕业证书、硕士学位证书、本科毕业证书、本科学位证书。"
            "本材料应识别为学校发放的毕业证书/学位证书，而不是学信网在线验证报告；"
            "请分别判断是否检测到毕业证书、学位证书，如果只检测到其中一种，标记缺少另一种。"
        ),
        "overseas_degree_certification": "附件1第9页为教育部留学服务中心国外学历学位认证书示例。",
        "hukou_material": "附件1第10-12页为户口本首页、个人页、个人信息变更页示例。",
        "professional_certificate": "附件1第13页为职称证书示例。",
        "award_certificate": "附件1第14页为获奖证明示例。",
        "securities_statement": "附件1第15-16页为中登账户状态查询截图、销户证明或未开户证明示例。",
        "profile_photo_intro": "附件1第17页为新员工风采展示个人照片示例。",
        "resignation_certificate": "附件1第18页为离职证明示例，需识别离职/解除/终止日期和单位盖章。",
        "labor_termination_form": "附件1第19页为上海退工单示例。",
        "employment_manual": "附件1第20-23页为就业创业证/劳动手册示例。",
        "bank_card": (
            "工资卡应通过图像识别确认是中国工商银行/ICBC 的银行卡；"
            "银行卡卡面通常没有姓名，姓名缺失不能作为不合格原因。"
        ),
    }
    return references.get(material_type, "请结合附件1上传文件示例判断材料外观、页数、清晰度和材料类型是否匹配。")
