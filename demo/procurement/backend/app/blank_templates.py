"""Recognize wholly unfilled supporting forms from their complete parsed text.

This is deliberately an allowlist, not a short-document or filename classifier.
An unmatched character (including a company, amount, table cell or review result)
keeps the document eligible for extraction. Parser warnings also veto skipping.
The original file and parsed text must still be retained by the caller.
"""
from __future__ import annotations

import re
import unicodedata


BLANK_TEMPLATE_POLICY = 'blank-supporting-v2'

# Only parser-generated location markers may disappear. In particular, do not
# remove arbitrary bracketed text: it may contain a bidder, model or a warning.
_LOCATION = re.compile(
    r'\[(?:PAGE:\d+(?:\s+LINE:\d+)?|TABLE:\d+\s+ROW:\d+)\]'
)
_NUMBERED_HEADING = re.compile(r'^(?:[一二三四五六七八九十]+|\d{1,2})[、.．]')

# These headings alone carry no filled response, awarded item or bidder/review
# result. They were observed as whole local parsed documents. Do not expand this
# to arbitrary short headings or to quote/review headings.
_SUPPORTING_HEADINGS = frozenset({
    '培训方案', '人员培训方案', '项目培训方案', '售后服务方案', '售后服务',
    '售后服务承诺', '实施方案', '项目实施方案', '服务实施方案', '技术服务方案',
    '项目组织实施方案', '项目管理和实施方案', '总体技术方案', '总体服务方案',
    '整体服务方案', '服务方案', '运维服务方案', '监理实施方案', '测评实施方案',
    '测评方案', '软件评测方案', '等保测评方案', '设计方案', '整改方案',
    '应急预案', '突发事件应急预案', '应急保障方案', '应急保障措施',
    '安全管理方案', '安全保密措施', '保密方案', '保密措施', '质量保证措施',
    '服务保障措施', '项目需求分析', '需求理解', '项目理解', '项目进度计划及保证措施',
    '工作流程及进度安排', '安装及调试', '其他材料', '其他补充材料',
    '供应商认为需要提交的其他材料', '供应商认为需要提交的其他材料(格式自拟)',
    '法定代表人授权委托书', '法定代表人资格证明书',
})
_EMPTY_INSTRUCTIONS = frozenset({
    '格式自拟', '(格式自拟)', '自拟',
    '供应商根据磋商文件要求自行提供,格式自拟',
    '根据招标文件评审细则及标准编制,格式自拟',
    '根据采购需求及评审细则自行编制',
    '(根据采购需求及评审细则自行编制)',
    '供应商根据自身情况按照评分办法要求的评分项的内容进行上传,格式自拟,由供应商自行编写',
})

# Complete boilerplate, with every variable still represented by a placeholder
# and every signature/contact field empty. Exact matching is intentional: a
# filled name or an attached quote must cause the model path to be retained.
_EMPTY_FORMS = (
    '''五、法定代表人授权委托书（如有）
本人 （姓名）系 （供应商名称）的法定代表人，现委托 （姓名）为我方代理人。
代理人根据授权，以我方名义签署、澄清、说明、补正、递交、撤回、修改 （项目名称）响应文件、签订合同和处理有关事宜，其法律后果由我方承担。
委托期限： 。
代理人无转委托权。
附：法定代表人身份证复印件及授权委托人身份证复印件
供应商： （盖单位章）
法定代表人： （签字或盖章）
身份证号码：
授权委托人： （签字或盖章）
身份证号码：
年 月 日''',
    '''具备履行合同所必需设备和专业技术能力声明函
我公司（公司名称）具有履行政府采购合同所必需的设备和专业技术能力。
特此声明。
供应商名称（加盖公章）：
日期：''',
    '''商务应答表
投标人按招标文件要求，自行拟定和编写商务应答表。''',
)


def _compact(text: str) -> str:
    value = unicodedata.normalize('NFKC', text).lstrip('\ufeff')
    return re.sub(r'\s+', '', _LOCATION.sub('', value))


def _without_end_punctuation(value: str) -> str:
    return value.rstrip(':。.;；')


# These additional whole-document forms were inspected in the local parsed
# corpus. Keep the placeholders, duplicated headings and literal page footer in
# the match: removing them broadly could hide a filled company, date or amount.
_ADDITIONAL_EXACT_FORMS = (
    "供应商自觉抵制政府采购领域商业贿赂行为承诺书采购人或采购代理机构:开展治理政府采购领域商业贿赂专项工作,是中央确定的治理商业贿赂六个重点领域之一,它既是完善市场经济、构建社会主义和谐社会的客观需要,又是从源头上抑制腐败的有力措施,意义重大、影响深远。为深入贯彻落实中央和省委、省政府的有关部署及要求,进一步规范政府采购行为,营造公平竞争的政府采购市场环境,维护政府采购制度良好声誉,在参与采购代理机构组织的政府采购活动中,我方庄重承诺:一、依法参与政府采购活动,遵纪守法,诚信经营,公平竞争。二、不向采购单位、采购代理机构和政府采购评审专家提供任何形式的商业贿赂;对索取或接受商业贿赂的单位和个人,及时向政府采购监督管理部门和纪检监察机关举报。三、不以提供虚假资质文件等形式参与政府采购活动,不以虚假材料谋取中标。四、不采取不正当手段诋毁、排挤其它投标人,与其它参与政府采购活动投标人保持良性的竞争关系。五、不与采购单位、采购代理机构和政府采购评审专家恶意串通,自觉维护政府采购公平竞争的市场秩序。六、不与其它投标人串通采取围标、陪标等商业欺诈手段谋取中标,积极维护国家利益、社会公共利益和采购单位的合法权益。七、严格履行政府采购合同约定义务,不在政府采购合同执行过程中采取降低质量或标准、减少数量、拖延交付时间等方式损害采购单位的利益,并自觉承担违约责任。八、自觉接受并积极配合政府采购监督管理部门和纪检监察机关依法实施的监督检查,如实反映情况,及时提供有关证明材料。供应商名称(加盖公章):日期:",
    "投标人自觉抵制政府采购领域商业贿赂行为承诺书采购人或采购代理机构:开展治理政府采购领域商业贿赂专项工作,是中央确定的治理商业贿赂六个重点领域之一,它既是完善市场经济、构建社会主义和谐社会的客观需要,又是从源头上抑制腐败的有力措施,意义重大、影响深远。为深入贯彻落实中央和省委、省政府的有关部署及要求,进一步规范政府采购行为,营造公平竞争的政府采购市场环境,维护政府采购制度良好声誉,在参与采购代理机构组织的政府采购活动中,我方庄重承诺:一、依法参与政府采购活动,遵纪守法,诚信经营,公平竞争。二、不向采购单位、采购代理机构和政府采购评审专家提供任何形式的商业贿赂;对索取或接受商业贿赂的单位和个人,及时向政府采购监督管理部门和纪检监察机关举报。三、不以提供虚假资质文件等形式参与政府采购活动,不以虚假材料谋取中标。四、不采取不正当手段诋毁、排挤其它投标人,与其它参与政府采购活动投标人保持良性的竞争关系。五、不与采购单位、采购代理机构和政府采购评审专家恶意串通,自觉维护政府采购公平竞争的市场秩序。六、不与其它投标人串通采取围标、陪标等商业欺诈手段谋取中标,积极维护国家利益、社会公共利益和采购单位的合法权益。七、严格履行政府采购合同约定义务,不在政府采购合同执行过程中采取降低质量或标准、减少数量、拖延交付时间等方式损害采购单位的利益,并自觉承担违约责任。八、自觉接受并积极配合政府采购监督管理部门和纪检监察机关依法实施的监督检查,如实反映情况,及时提供有关证明材料。投标人名称(加盖公章):日期:",
    "投标文件投标文件采购项目名称:采购项目名称:{项目名称}{项目名称}采购项目编号:采购项目编号:{项目编码}{项目编码}采购包号:采购包号:{包号}{包号}投标人名称:{供应商名称}投标人名称:{供应商名称}日期:{当前日期}日期:{当前日期}-第1⻚-",
    "响应文件响应文件采购项目名称:采购项目名称:{项目名称}{项目名称}采购项目编号:采购项目编号:{项目编码}{项目编码}采购包号:采购包号:{包号}{包号}供应商名称:{供应商名称}供应商名称:{供应商名称}日期:{当前日期}日期:{当前日期}-第1⻚-",
    "投标文件投标文件采购项目名称:{请填写采购项目名称}采购项目编号:{请填写采购项目编号}采购包号:{请填写采购包号}投标人名称:{XXX}日期:{XXXX}年{XX}月{XX}日-第1⻚-",
    "投标文件投标文件采购项目名称:采购项目名称:{项目名称}{项目名称}采购项目编号:采购项目编号:{项目编码}{项目编码}采购包号:采购包号:{包号}{包号}投标人名称:{供应商名称}投标人名称:{供应商名称}日期:{当前日期}日期:{当前日期}-第1页-",
    "响应文件响应文件采购项目名称:采购项目名称:{项目名称}{项目名称}采购项目编号:采购项目编号:{项目编码}{项目编码}采购包号:采购包号:{包号}{包号}供应商名称:{供应商名称}供应商名称:{供应商名称}日期:{当前日期}日期:{当前日期}-第1页-",
    "(根据采购需求及评审细则自行编制。)",
    "技术标格式自拟;",
    "1、项目实施方案2、培训方案3、售后服务方案及承诺",
    "其他证明材料(若有)(自由格式)除招标文件另有规定外,投标人认为需要提交的其他证明材料或资料(如技术方案等)加盖投标人的单位公章后应在此项下提交。",
    "其他证明材料(若有)(自由格式)除采购文件另有规定外,供应商认为需要提交的其他证明材料或资料(如工程量清单等)加盖供应商的单位公章后应在此项下提交。",
    "其他证明材料(若有)(自由格式)除采购文件另有规定外,供应商认为需要提交的其他证明材料或资料(如技术方案等)加盖供应商的单位公章后应在此项下提交。",
    "其他证明材料(若有)(自由格式)除采购文件另有规定外,供应商认为需要提交的其他证明材料或资料加盖供应商的单位公章后应在此项下提交。",
    "其他证明材料(若有)(自由格式)除磋商文件另有规定外,供应商认为需要提交的其他证明材料或资料加盖供应商的单位公章后应在此项下提交。",
    "其他证明材料(若有)(自由格式)除采购文件另有规定外,供应商认为需要提交的其他证明材料或资料(若有)加盖供应商的单位公章后应在此项下提交。",
    "进度保证措施详见“第五章评标办法“5.4.2.评标细则及标准”条款规定。",
    "拟投入本项目人员配备详见“第五章评标办法“5.4.2.评标细则及标准”条款规定。",
    "质量保证措施详见“第五章评标办法“5.4.2.评标细则及标准”条款规定。",
    "售后服务方案详见“第五章评标办法“5.4.2.评标细则及标准”条款规定。",
    "项目组织实施方案详见“第五章评标办法“5.4.2.评标细则及标准”条款规定。",
    "培训方案详见“第五章评标办法“5.4.2.评标细则及标准”条款规定。",
    "对本项目的认识及理解详见“第五章评标办法“5.4.2.评标细则及标准”条款规定。",
    "平台建设方案详见“第五章评标办法“5.4.2.评标细则及标准”条款规定。",
)
_EXACT_EMPTY_FORMS = frozenset(_compact(form) for form in (*_EMPTY_FORMS, *_ADDITIONAL_EXACT_FORMS))
_COVER_TITLE = re.compile(r'^(?:投标文件|响应文件|投标响应文件)(?:\((?:正本|副本)\)|正本|副本)?')
_COVER_FIELD = re.compile(
    r'(?P<label>项目编号|项目名称|项目包号|包号|投标人名称|供应商名称|日期)'
    r'(?:\((?:电子公章|公章|加盖公章|盖单位章)\))?'
    r':?[_＿]*(?:年月日)?[:。;；]*'
)


def _is_empty_cover(value: str) -> bool:
    heading = _COVER_TITLE.match(value)
    if not heading:
        return False
    remainder = value[heading.end():]
    labels: set[str] = set()
    while remainder:
        field = _COVER_FIELD.match(remainder)
        if not field:
            return False
        labels.add(field['label'])
        remainder = remainder[field.end():]
    return (
        {'项目编号', '项目名称'}.issubset(labels)
        and bool({'投标人名称', '供应商名称'} & labels)
    )


def classify_blank_template(filename: str, text: str) -> str | None:
    """Return an auditable skip reason only for complete known empty content.

    ``filename`` is accepted for the routing API but never proves emptiness.
    Empty parses, warning-bearing parses, unknown templates, populated fields,
    quote tables and review outcomes return ``None`` and keep normal extraction.
    """
    if not text or '[PARSE_WARNING]' in text or len(text) > 32_000:
        return None
    value = _compact(text)
    if not value or '[' in value or ']' in value:
        return None
    if value in _EXACT_EMPTY_FORMS:
        proof = '完整文本精确匹配已核验的未填写支持模板'
    elif _is_empty_cover(value):
        proof = '封面仅含项目和供应商空字段,没有已填写值'
    else:
        title = _without_end_punctuation(_NUMBERED_HEADING.sub('', value))
        if title in _EMPTY_INSTRUCTIONS:
            proof = '全文仅为填写说明,没有实际响应内容'
        else:
            for suffix in ('(格式自拟)', '格式自拟'):
                if title.endswith(suffix):
                    title = _without_end_punctuation(title[:-len(suffix)])
                    break
            if title not in _SUPPORTING_HEADINGS:
                return None
            proof = '全文仅为支持材料标题或自拟提示,没有实际响应内容'
    return f'{BLANK_TEMPLATE_POLICY}: {proof}；保留原文和解析结果供复核'
