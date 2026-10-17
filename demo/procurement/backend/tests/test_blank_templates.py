import pytest

from app.blank_templates import BLANK_TEMPLATE_POLICY, classify_blank_template


@pytest.mark.parametrize('text', [
    '培训方案', '培训方案：', '一、售后服务方案', '3.实施方案（格式自拟）',
    '[PAGE:1]\n[PAGE:1 LINE:1] 应急预案',
    '[TABLE:1 ROW:1] 格式自拟',
    '根据采购需求及评审细则自行编制。',
    '供应商根据磋商文件要求自行提供，格式自拟。',
    '供应商认为需要提交的其他材料。（格式自拟）',
])
def test_only_exact_empty_title_or_instruction_is_skipped(text):
    reason = classify_blank_template('匿名附件.docx', text)
    assert reason and reason.startswith(BLANK_TEMPLATE_POLICY + ':')
    assert '保留原文' in reason


@pytest.mark.parametrize('text', [
    '投标文件\n项目编号：\n项目名称：\n项目包号：\n投标人名称（电子公章）：\n日期',
    '响应文件 项目名称：_____ 项目编号：_____ 供应商名称（公章）：_____ 日期： 年 月 日',
])
def test_cover_requires_all_identifying_fields_to_be_empty(text):
    assert classify_blank_template('封面.docx', text)


@pytest.mark.parametrize('text', [
    '', '项目名称：', '封面', '投标文件', '投标文件 项目名称：项目编号：',
    '投标文件 项目编号：2026-001 项目名称：设备采购 投标人名称：甲公司 日期：',
    '投标文件 项目编号： 项目名称： 投标人名称（电子公章）：山东甲乙有限公司 日期：',
    '投标文件 项目编号： 项目名称： 投标人名称（电子公章）： 日期：2026年1月1日',
    '售后服务方案\n本公司承诺质保期3年。',
    '培训方案\n供应商：山东甲乙有限公司',
    '培训方案\n分项报价表\n货物名称 | 品牌 | 型号 | 数量 | 单价 | 金额\n服务器 | 华为 | ABC | 2 | 10000 | 20000',
    '法定代表人授权委托书\n本人张三系山东甲乙有限公司的法定代表人。',
    '供应商资格审查统计表\n供应商名称：\n资格审查：合格',
    '符合性审查其他文件\n[TABLE:1 ROW:1] 序号 | 审查因素 | 审查内容 | 响应\n[TABLE:1 ROW:2] 1 | 报价 | 满足要求 | 是',
    '得分汇总表\n甲公司 90分',
    '残疾人福利性单位声明函\n本单位不属于残疾人福利性单位',
    '中小企业声明函\n无',
    '企业具有履行合同所必需的设备和专业技术能力。',
    '未经核验的新模板', '请提供报价明细', '培训方案\n[PARSE_WARNING] 部分页面不可读取',
    '[OCR_WARNING]培训方案', '[未知字段:华为]培训方案',
    '培训方案' + ' ' * 32_000,
])
def test_filled_mixed_review_ambiguous_and_incomplete_parses_stay_eligible(text):
    assert classify_blank_template('空白模板.docx', text) is None


EMPTY_DECLARATION = '''具备履行合同所必需设备和专业技术能力声明函
我公司（公司名称）具有履行政府采购合同所必需的设备和专业技术能力。
特此声明。
供应商名称（加盖公章）：
日期：'''


def test_full_boilerplate_requires_every_placeholder_and_field_unfilled():
    assert classify_blank_template('能力声明.docx', EMPTY_DECLARATION)
    assert classify_blank_template('能力声明.docx', EMPTY_DECLARATION.replace('（公司名称）', '（山东甲乙有限公司）')) is None
    assert classify_blank_template('能力声明.docx', EMPTY_DECLARATION + '2026年10月17日') is None
    assert classify_blank_template('能力声明.docx', EMPTY_DECLARATION + '\n分项报价表 服务器 华为 ABC 2台 20000元') is None


def test_filename_and_document_length_never_prove_emptiness():
    assert classify_blank_template('培训方案.docx', '甲公司 报价100元') is None
    assert classify_blank_template('空白封面.docx', '') is None
    assert classify_blank_template('scan-123.docx', '培训方案')


def test_complete_blank_authorization_is_auditable_and_filled_copy_is_retained():
    text = '''五、法定代表人授权委托书（如有）
本人 （姓名）系 （供应商名称）的法定代表人，现委托 （姓名）为我方代理人。
代理人根据授权，以我方名义签署、澄清、说明、补正、递交、撤回、修改 （项目名称）响应文件、签订合同和处理有关事宜，其法律后果由我方承担。
委托期限： 。代理人无转委托权。
附：法定代表人身份证复印件及授权委托人身份证复印件
供应商： （盖单位章）法定代表人： （签字或盖章）身份证号码：
授权委托人： （签字或盖章）身份证号码： 年 月 日'''
    assert classify_blank_template('授权书.docx', text)
    assert classify_blank_template('授权书.docx', text.replace('（供应商名称）', '华为技术有限公司')) is None
    assert classify_blank_template('授权书.docx', text.replace('委托期限： 。', '委托期限： 30日。')) is None


def test_unknown_bracketed_text_is_not_discarded_as_parser_metadata():
    assert classify_blank_template('附件.docx', '[供应商:甲公司]培训方案') is None
    assert classify_blank_template('附件.docx', '[PAGE:1 LINE:1] 培训方案\n[PAGE:1 LINE:2] 甲公司 100元') is None
    # A worksheet title is source content, not just a numeric location. It may
    # carry the supplier even when the only cell looks like a blank heading.
    assert classify_blank_template('附件.xlsx', '[SHEET:甲公司 ROW:1] 培训方案') is None

PLACEHOLDER_COVER = "投标文件投标文件采购项目名称:采购项目名称:{项目名称}{项目名称}采购项目编号:采购项目编号:{项目编码}{项目编码}采购包号:采购包号:{包号}{包号}投标人名称:{供应商名称}投标人名称:{供应商名称}日期:{当前日期}日期:{当前日期}-第1⻚-"
EMPTY_ANTI_BRIBERY = "供应商自觉抵制政府采购领域商业贿赂行为承诺书采购人或采购代理机构:开展治理政府采购领域商业贿赂专项工作,是中央确定的治理商业贿赂六个重点领域之一,它既是完善市场经济、构建社会主义和谐社会的客观需要,又是从源头上抑制腐败的有力措施,意义重大、影响深远。为深入贯彻落实中央和省委、省政府的有关部署及要求,进一步规范政府采购行为,营造公平竞争的政府采购市场环境,维护政府采购制度良好声誉,在参与采购代理机构组织的政府采购活动中,我方庄重承诺:一、依法参与政府采购活动,遵纪守法,诚信经营,公平竞争。二、不向采购单位、采购代理机构和政府采购评审专家提供任何形式的商业贿赂;对索取或接受商业贿赂的单位和个人,及时向政府采购监督管理部门和纪检监察机关举报。三、不以提供虚假资质文件等形式参与政府采购活动,不以虚假材料谋取中标。四、不采取不正当手段诋毁、排挤其它投标人,与其它参与政府采购活动投标人保持良性的竞争关系。五、不与采购单位、采购代理机构和政府采购评审专家恶意串通,自觉维护政府采购公平竞争的市场秩序。六、不与其它投标人串通采取围标、陪标等商业欺诈手段谋取中标,积极维护国家利益、社会公共利益和采购单位的合法权益。七、严格履行政府采购合同约定义务,不在政府采购合同执行过程中采取降低质量或标准、减少数量、拖延交付时间等方式损害采购单位的利益,并自觉承担违约责任。八、自觉接受并积极配合政府采购监督管理部门和纪检监察机关依法实施的监督检查,如实反映情况,及时提供有关证明材料。供应商名称(加盖公章):日期:"


def test_placeholder_cover_requires_literal_placeholders_and_page_footer():
    assert classify_blank_template('scan.pdf', PLACEHOLDER_COVER)
    for old, new in (
        ('{供应商名称}', '山东甲乙有限公司'),
        ('{项目名称}', '服务器采购'),
        ('{包号}', '1'),
        ('{当前日期}', '2026年10月17日'),
        ('-第1⻚-', '-第100元-'),
    ):
        assert classify_blank_template('scan.pdf', PLACEHOLDER_COVER.replace(old, new, 1)) is None
    assert classify_blank_template('scan.pdf', PLACEHOLDER_COVER + '报价100元') is None


def test_unsigned_boilerplate_keeps_any_filled_signature_or_additional_fact():
    assert classify_blank_template('承诺书.docx', EMPTY_ANTI_BRIBERY)
    assert classify_blank_template('承诺书.docx', EMPTY_ANTI_BRIBERY.replace(
        '供应商名称(加盖公章):日期:', '供应商名称(加盖公章):山东甲乙有限公司日期:')) is None
    assert classify_blank_template('承诺书.docx', EMPTY_ANTI_BRIBERY.replace(
        '日期:', '日期:2026年10月17日')) is None
    assert classify_blank_template('承诺书.docx', EMPTY_ANTI_BRIBERY + '\n投标人甲公司报价100元') is None
    assert classify_blank_template('承诺书.docx', EMPTY_ANTI_BRIBERY + '\n[PARSE_WARNING]后页不可读取') is None


@pytest.mark.parametrize('text', [
    '(根据采购需求及评审细则自行编制。)',
    '技术标格式自拟;',
    '1、项目实施方案2、培训方案3、售后服务方案及承诺',
    "培训方案详见“第五章评标办法“5.4.2.评标细则及标准”条款规定。",
    "其他证明材料(若有)(自由格式)除招标文件另有规定外,投标人认为需要提交的其他证明材料或资料(如技术方案等)加盖投标人的单位公章后应在此项下提交。",
])
def test_complete_audited_instruction_patterns_reject_added_facts(text):
    assert classify_blank_template('附件.docx', text)
    assert classify_blank_template('附件.docx', text + '供应商:山东甲乙有限公司') is None
    assert classify_blank_template('附件.docx', text + '\n分项报价表 服务器 华为 ABC 1 100 100') is None


def test_no_generalized_placeholder_or_footer_erasure():
    # Unknown placeholder-bearing content and factual page text remain eligible.
    assert classify_blank_template('附件.pdf', '投标文件{未知实际内容}-第1页-') is None
    assert classify_blank_template('附件.pdf', '培训方案-第100元-') is None
    assert classify_blank_template('附件.pdf', '培训方案\n100') is None
