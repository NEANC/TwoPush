#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""utils 模块单元测试"""

import pytest

import modules.utils as utils
from modules.utils import mask_sensitive_fields, parse_time_string
from tests import make_mobile_number, mask_mobile_number


def test_mask_sensitive_fields_masks_only_declared_fields():
    """仅对调用方声明的字段执行敏感片段脱敏"""
    mobile = make_mobile_number()
    fields = {
        'reason': f'手机号 {mobile} access_token=abc sign=xyz secret=SECa token=xyz',
        'provider': 'dingtalk(builtin)',
        'content': 'design=keep assign=keep xaccess_token=keep',
    }
    masked = mask_sensitive_fields(fields, sensitive_fields={'reason'})

    assert mobile not in masked['reason']
    assert mask_mobile_number(mobile) in masked['reason']
    assert 'access_token=***' in masked['reason']
    assert ' token=***' in masked['reason']
    assert 'token=xyz' not in masked['reason']
    assert 'sign=***' in masked['reason']
    assert 'secret=***' in masked['reason']
    assert 'xaccess_token=keep' in masked['content']
    assert masked['provider'] == 'dingtalk(builtin)'
    assert masked['content'] == fields['content']


def test_mask_sensitive_fields_validates_input_and_returns_copy():
    """校验入参类型并返回新字典，不修改原字段"""
    mobile = make_mobile_number()
    fields = {'reason': mobile, 'count': int(mobile), 'empty': None}
    result = mask_sensitive_fields(fields, sensitive_fields={'reason', 'count', 'empty'})

    assert result is not fields
    assert fields['reason'] == mobile
    assert result['reason'] == mask_mobile_number(mobile)
    assert result['count'] == mask_mobile_number(mobile)
    assert result['empty'] is None
    assert mask_sensitive_fields(fields, sensitive_fields=set()) == fields
    assert mask_sensitive_fields(fields, sensitive_fields=set()) is not fields

    with pytest.raises(TypeError):
        mask_sensitive_fields([], sensitive_fields={'reason'})
    with pytest.raises(TypeError):
        mask_sensitive_fields(fields, sensitive_fields='reason')


def test_mask_sensitive_fields_keeps_non_mobile_segments_untouched():
    """1 开头但第二位非 3-9 的 11 位数字（如订单号）不应被脱敏"""
    text = mask_sensitive_fields({'text': '订单号 12000000000'}, {'text'})['text']

    assert '12000000000' in text


def test_mask_sensitive_fields_still_masks_valid_mobile_segments():
    """11 位且第二位为 3-9 的手机号仍应脱敏为保留首 3 位与末 4 位的形式"""
    mobile = make_mobile_number()
    text = mask_sensitive_fields({'text': f'手机号 {mobile}'}, {'text'})['text']

    assert mobile not in text
    assert mask_mobile_number(mobile) in text


def test_mask_sensitive_fields_masks_country_code_prefixed_mobiles():
    """带 +86/86 前缀的手机号应脱敏为纯号段掩码，不泄露完整号码"""
    mobile = make_mobile_number()
    text = mask_sensitive_fields(
        {'t': f'手机号 +86{mobile} 备用 86{mobile}'}, {'t'}
    )['t']

    assert mobile not in text
    assert text.count(mask_mobile_number(mobile)) == 2


def test_ampersand_in_sensitive_value_is_fully_masked():
    """敏感值内含 & 时应完整脱敏，不残留 & 后缀片段"""
    result = mask_sensitive_fields({'reason': 'secret=abc&def'}, {'reason'})['reason']

    assert result == 'secret=***'


def test_query_chain_is_fully_masked():
    """含 & 的连续敏感键值对（如 query 链）应整体被脱敏"""
    result = mask_sensitive_fields(
        {'reason': 'access_token=abc&sign=xyz'}, {'reason'}
    )['reason']

    assert result == 'access_token=***&sign=***'
    assert 'abc' not in result
    assert 'xyz' not in result


def test_non_sensitive_query_param_after_ampersand_is_kept():
    """& 后紧跟非敏感 key= 形态时该 & 为参数分隔符，非敏感参数须保留"""
    result = mask_sensitive_fields(
        {'reason': 'access_token=abc&foo=bar'}, {'reason'}
    )['reason']

    assert result == 'access_token=***&foo=bar'


def test_query_param_boundary_keeps_surrounding_text():
    """敏感值按 & 边界结束时，其前后的普通文本片段应原样保留"""
    result = mask_sensitive_fields(
        {'reason': 'prefix token=abc&a=1 tail'}, {'reason'}
    )['reason']

    assert result == 'prefix token=***&a=1 tail'


def test_consecutive_sensitive_query_params_are_masked_separately():
    """连续多个敏感 query 参数应各自脱敏，不出现星号数量畸形的输出"""
    two = mask_sensitive_fields({'reason': 'token=a&sign=b'}, {'reason'})['reason']
    three = mask_sensitive_fields(
        {'reason': 'token=a&sign=b&auth=c'}, {'reason'}
    )['reason']

    assert two == 'token=***&sign=***'
    assert three == 'token=***&sign=***&auth=***'


def test_dingtalk_signed_url_keeps_timestamp_param():
    """钉钉签名 URL 中凭据须脱敏，而 timestamp 等非敏感参数完整保留"""
    result = mask_sensitive_fields(
        {
            'reason': (
                'https://oapi.dingtalk.com/robot/send?'
                'access_token=REALTOKEN123&timestamp=1700000000000&'
                'sign=REALSIGNabc failed'
            )
        },
        {'reason'},
    )['reason']

    assert 'REALTOKEN123' not in result
    assert 'REALSIGNabc' not in result
    assert 'timestamp=1700000000000' in result


def test_whitespace_separated_sensitive_values_are_masked():
    """空白分隔的敏感键值对各自被脱敏，空白作为分隔符保留"""
    result = mask_sensitive_fields(
        {'reason': 'token=xyz secret=SECa'}, {'reason'}
    )['reason']

    assert 'token=***' in result
    assert 'secret=***' in result
    assert 'xyz' not in result
    assert 'SECa' not in result


def test_cjk_prefix_before_sensitive_key_is_masked():
    """中文（非 ASCII）直接粘连敏感键名时仍应脱敏"""
    result = mask_sensitive_fields(
        {'reason': '参数access_token=abc 密钥secret=SECa 签名sign=xyz'}, {'reason'}
    )['reason']

    assert 'abc' not in result
    assert 'SECa' not in result
    assert 'xyz' not in result
    assert 'access_token=***' in result
    assert 'secret=***' in result
    assert 'sign=***' in result


def test_ascii_prefix_before_sensitive_key_not_masked():
    """ASCII 字母数字下划线前缀（如 xaccess_token=）不应被脱敏"""
    result = mask_sensitive_fields(
        {'reason': 'xaccess_token=keep mytoken=keep'}, {'reason'}
    )['reason']

    assert 'xaccess_token=keep' in result
    assert 'mytoken=keep' in result


def test_json_key_value_sensitive_fields_are_masked():
    """JSON 键值形式（"token":"value"）的敏感值应整体脱敏"""
    result = mask_sensitive_fields(
        {'reason': '{"access_token":"abc123","secret":"SECxyz","sign":"sigxyz"}'},
        {'reason'},
    )['reason']

    assert result == '{"access_token":"***","secret":"***","sign":"***"}'
    assert 'abc123' not in result
    assert 'SECxyz' not in result
    assert 'sigxyz' not in result


def test_colon_form_sensitive_values_are_masked():
    """冒号形式（token: value）的敏感值应脱敏，冒号两侧空格可有可无"""
    result = mask_sensitive_fields(
        {'reason': 'token: xyz sign : SECa'}, {'reason'}
    )['reason']

    assert 'token=***' in result
    assert 'sign=***' in result
    assert 'xyz' not in result
    assert 'SECa' not in result


def test_quoted_value_with_ampersand_is_fully_masked():
    """JSON 引号形式的值内含 & 时应完整脱敏，不残留 & 后缀片段"""
    result = mask_sensitive_fields(
        {'reason': '"secret":"abc&def"'}, {'reason'}
    )['reason']

    assert result == '"secret":"***"'
    assert '&def' not in result


def test_json_keys_with_sensitive_name_substrings_not_masked():
    """design/assign/mytoken 等含敏感键名子串的键不应被误伤"""
    result = mask_sensitive_fields(
        {'reason': '{"design":"keep","assign":"keep","mytoken":"keep"}'},
        {'reason'},
    )['reason']

    assert result == '{"design":"keep","assign":"keep","mytoken":"keep"}'


def test_json_value_with_escaped_quote_is_fully_masked():
    """JSON 引号形式的值内含转义引号时应完整脱敏，不残留引号后片段"""
    result = mask_sensitive_fields(
        {'reason': '{"secret":"abc\\"def"}'}, {'reason'}
    )['reason']

    assert result == '{"secret":"***"}'
    assert 'abc"def' not in result


def test_json_value_with_comma_is_fully_masked():
    """JSON 引号形式的值内含逗号时应完整脱敏，不残留逗号后片段"""
    result = mask_sensitive_fields(
        {'reason': '{"secret":"a,b"}'}, {'reason'}
    )['reason']

    assert result == '{"secret":"***"}'
    assert 'a' not in result
    assert 'b' not in result


def test_mixed_bare_and_json_forms_are_masked_respectively():
    """裸值形式与 JSON 引号形式同时存在时，各自按对应规则脱敏"""
    result = mask_sensitive_fields(
        {'reason': 'access_token=abc {"secret":"SECa","sign":"xyz"}'},
        {'reason'},
    )['reason']

    assert 'access_token=***' in result
    assert '"secret":"***"' in result
    assert '"sign":"***"' in result
    assert 'abc' not in result
    assert 'SECa' not in result
    assert 'xyz' not in result


@pytest.mark.parametrize('key', ['access_token', 'secret', 'token'])
def test_single_quoted_dict_sensitive_fields_are_masked(key):
    """单引号字典中的敏感键值应完整脱敏并保留单引号结构"""
    result = mask_sensitive_fields(
        {'reason': f"{{'{key}': 'top-secret'}}"}, {'reason'}
    )['reason']

    assert result == f"{{'{key}': '***'}}"
    assert 'top-secret' not in result


def test_single_quoted_value_with_escaped_quote_is_fully_masked():
    """单引号值内含转义单引号时应完整脱敏，不残留引号后片段"""
    result = mask_sensitive_fields(
        {'reason': r"{'secret': 'SEC\'xyz'}"}, {'reason'}
    )['reason']

    assert result == "{'secret': '***'}"
    assert 'SEC' not in result
    assert 'xyz' not in result


def test_single_quoted_value_with_comma_is_fully_masked():
    """单引号值内含逗号时应完整脱敏，不残留逗号后片段"""
    result = mask_sensitive_fields(
        {'reason': "'token': 'abc,def'"}, {'reason'}
    )['reason']

    assert result == "'token': '***'"
    assert 'abc' not in result
    assert 'def' not in result


def test_mixed_single_and_double_quoted_forms_are_masked():
    """单双引号敏感键值混合出现时应分别保留原有结构"""
    result = mask_sensitive_fields(
        {'reason': "{'access_token': 'abc', \"secret\":\"SECxyz\"}"},
        {'reason'},
    )['reason']

    assert result == "{'access_token': '***', \"secret\":\"***\"}"


def test_single_quoted_sensitive_name_substrings_are_not_masked():
    """单引号键中含敏感键名子串时不应误伤"""
    original = (
        "{'design': 'keep', 'assign': 'keep', 'mytoken': 'keep', "
        "'xaccess_token': 'keep'}"
    )
    result = mask_sensitive_fields({'reason': original}, {'reason'})['reason']

    assert result == original


@pytest.mark.parametrize(
    ('original', 'expected'),
    [
        ("{'secret': 'top-secret", "{'secret': '***"),
        ('{"secret": "top-secret', '{"secret": "***'),
    ],
)
def test_unclosed_quoted_sensitive_values_are_masked_without_closing_quote(
    original, expected
):
    """未闭合单双引号敏感值应脱敏到末尾且不补闭合引号"""
    result = mask_sensitive_fields({'reason': original}, {'reason'})['reason']

    assert result == expected
    assert 'top-secret' not in result


@pytest.mark.parametrize(
    ('original', 'expected'),
    [
        ('{"secret\': \'top-secret', '{"secret\': \'***'),
        ('{\'secret": "top-secret', '{\'secret": "***'),
    ],
)
def test_mixed_key_quotes_with_unclosed_values_are_masked(original, expected):
    """敏感键两侧引号混用时仍应保守脱敏未闭合值"""
    result = mask_sensitive_fields({'reason': original}, {'reason'})['reason']

    assert result == expected
    assert 'top-secret' not in result


@pytest.mark.parametrize(
    ('original', 'expected'),
    [
        (r"{'secret': 'abc\'def", "{'secret': '***"),
        (r"{'secret': 'abc\\' tail", r"{'secret': '***' tail"),
    ],
)
def test_backslash_parity_controls_sensitive_value_quote_boundary(
    original, expected
):
    """奇数反斜杠转义引号，偶数反斜杠允许引号闭合"""
    result = mask_sensitive_fields({'reason': original}, {'reason'})['reason']

    assert result == expected
    assert 'abc' not in result


def test_single_quoted_sign_with_unclosed_value_is_masked():
    """单引号 sign 的未闭合敏感值应脱敏到字符串末尾"""
    original = "{'sign': 'top-secret"
    result = mask_sensitive_fields({'reason': original}, {'reason'})['reason']

    assert result == "{'sign': '***"
    assert 'top-secret' not in result


@pytest.mark.parametrize(
    'original',
    [
        "secret='abc\\",
        r"secret='abc\\",
        'secret="abc\\',
        r'secret="abc\\',
    ],
)
def test_unclosed_sensitive_value_ending_with_backslashes_is_masked(original):
    """未闭合单双引号值末尾一个或两个反斜杠时均应完整脱敏"""
    result = mask_sensitive_fields({'reason': original}, {'reason'})['reason']

    assert result == f'{original[:8]}***'
    assert 'abc' not in result


def test_truncated_consecutive_sensitive_text_ending_with_backslash_is_masked():
    """连续敏感文本在孤立反斜杠处截断时不应泄漏后续内容"""
    original = "secret='abc\\token=xyz\\"
    result = mask_sensitive_fields({'reason': original}, {'reason'})['reason']

    assert result == "secret='***"
    assert 'abc' not in result
    assert 'xyz' not in result


@pytest.mark.parametrize('key', ['access_token', 'token', 'sign', 'secret'])
@pytest.mark.parametrize('quote', ['"', "'"])
@pytest.mark.parametrize('line_ending', ['\n', '\r\n'], ids=['lf', 'crlf'])
@pytest.mark.parametrize(
    ('ending', 'expected_ending'),
    [
        ('closed', 'closed'),
        ('unclosed', 'unclosed'),
        ('truncated', 'truncated'),
    ],
)
def test_backslash_line_break_in_sensitive_quoted_value_is_fully_masked(
    key, quote, line_ending, ending, expected_ending
):
    """反斜杠换行后的敏感值在闭合、未闭合与末尾截断时均应完整脱敏"""
    prefix = f'{key}={quote}'
    if ending == 'closed':
        original = f'{prefix}before\\{line_ending}after{quote} keep=visible'
        expected = f'{prefix}***{quote} keep=visible'
    elif ending == 'unclosed':
        original = f'{prefix}before\\{line_ending}after'
        expected = f'{prefix}***'
    else:
        original = f'{prefix}before\\{line_ending}'
        expected = f'{prefix}***'

    result = mask_sensitive_fields({'reason': original}, {'reason'})['reason']

    assert result == expected, expected_ending
    assert 'before' not in result
    assert 'after' not in result


@pytest.mark.parametrize(
    ('original', 'secret', 'expected'),
    [
        # 分隔符百分号编码：= 编码为 %3D/%3d，: 编码为 %3A/%3a
        ('access_token%3DLEAKED', 'LEAKED', 'access_token%3D***'),
        ('sign%3DSIGNED', 'SIGNED', 'sign%3D***'),
        ('secret%3Avalue', 'value', 'secret%3A***'),
        ('access_token%3dLEAKED', 'LEAKED', 'access_token%3d***'),
        ('secret%3avalue', 'value', 'secret%3a***'),
        # 键名百分号编码：access_token 的下划线编码为 %5F/%5f
        ('access%5Ftoken=LEAKED', 'LEAKED', 'access%5Ftoken=***'),
        ('access%5ftoken=LEAKED', 'LEAKED', 'access%5ftoken=***'),
        # 键名与分隔符同时百分号编码
        ('access%5Ftoken%3DLEAKED', 'LEAKED', 'access%5Ftoken%3D***'),
        ('access%5ftoken%3dLEAKED', 'LEAKED', 'access%5ftoken%3d***'),
    ],
)
def test_percent_encoded_sensitive_key_value_is_masked(original, secret, expected):
    """百分号编码的敏感键名与分隔符应脱敏，并保留原始编码形式"""
    result = mask_sensitive_fields({'reason': original}, {'reason'})['reason']

    assert result == expected
    assert secret not in result


@pytest.mark.parametrize('line_ending', ['\n', '\r\n'], ids=['lf', 'crlf'])
def test_backslash_line_break_does_not_mask_sensitive_name_substrings(line_ending):
    """含敏感键名子串的普通键在反斜杠换行场景中不应被误伤"""
    original = (
        f'design="before\\{line_ending}after" '
        f'assign=\'before\\{line_ending}after\' '
        f'mytoken="before\\{line_ending}after" '
        f'xaccess_token=\'before\\{line_ending}after\''
    )

    result = mask_sensitive_fields({'reason': original}, {'reason'})['reason']

    assert result == original


@pytest.mark.parametrize(
    ('original', 'expected'),
    [
        # 键名中任意单字符的百分号编码：%74=t、%61=a、%65=e、%67=g
        ('access_%74oken=LEAKED', 'access_%74oken=***'),
        ('%61ccess_token=LEAKED', '%61ccess_token=***'),
        ('secr%65t=LEAKED', 'secr%65t=***'),
        ('si%67n=LEAKED', 'si%67n=***'),
        # 键名字母大小写折叠（access_%74OKEN 的 OKEN 为大写）
        ('access_%74OKEN=LEAKED', 'access_%74OKEN=***'),
        # hex 字母大小写：%6F 与 %6f 均表示 o
        ('t%6Fken=LEAKED', 't%6Fken=***'),
        ('t%6fken=LEAKED', 't%6fken=***'),
        # 下划线编码与字母编码混合
        ('access%5Fto%6Ben=LEAKED', 'access%5Fto%6Ben=***'),
        ('access%5fto%6ben=LEAKED', 'access%5fto%6ben=***'),
    ],
)
def test_percent_encoded_any_char_in_sensitive_key_is_masked(original, expected):
    """敏感键名中任意单字符的百分号编码应脱敏，并保留原始编码形式"""
    result = mask_sensitive_fields({'reason': original}, {'reason'})['reason']

    assert result == expected
    assert 'LEAKED' not in result


def test_percent_encoded_sensitive_key_substrings_not_masked():
    """百分号编码的敏感键名子串（xaccess_ 前缀）与 signature 等不应误伤"""
    original = 'xaccess_%74oken=keep signature=keep secretary=keep design=keep'

    result = mask_sensitive_fields({'reason': original}, {'reason'})['reason']

    assert result == original


def test_mask_sensitive_fields_masks_url_userinfo():
    """URL userinfo 中的用户名与密码应脱敏为 ***:***@，保留 scheme/host/port"""
    result = mask_sensitive_fields(
        {'reason': 'ProxyError http://alice:SuperSecret@127.0.0.1:9'}, {'reason'}
    )['reason']

    assert 'http://***:***@127.0.0.1:9' in result
    assert 'alice' not in result
    assert 'SuperSecret' not in result


def test_mask_sensitive_fields_masks_url_userinfo_without_password():
    """仅用户名的 URL userinfo 统一脱敏为 ***:***@"""
    result = mask_sensitive_fields(
        {'reason': 'https://bob@example.com:8443/api'}, {'reason'}
    )['reason']

    assert 'https://***:***@example.com:8443/api' in result
    assert 'bob' not in result


def test_mask_sensitive_fields_url_userinfo_does_not_touch_plain_email_or_mailto():
    """纯文本邮箱与 mailto: 形式不应被 URL userinfo 规则误伤"""
    original = '联系 a@b.com 或 mailto:c@d.com 均非 URL userinfo'
    result = mask_sensitive_fields({'reason': original}, {'reason'})['reason']

    assert result == original


def test_mask_sensitive_fields_extra_sensitive_keys_are_masked():
    """password/api_key/webhook 等扩展敏感键值对应脱敏为 ***"""
    result = mask_sensitive_fields(
        {
            'reason': 'password=MailPass api_key=ApiSecret '
            'webhook=https://hooks.example/SecretPath'
        },
        {'reason'},
    )['reason']

    assert 'password=***' in result
    assert 'api_key=***' in result
    assert 'webhook=***' in result
    assert 'MailPass' not in result
    assert 'ApiSecret' not in result
    assert 'SecretPath' not in result


def test_mask_sensitive_fields_extra_sensitive_keys_case_insensitive():
    """扩展敏感键名大小写折叠，PASSWORD/API_KEY/PassWd 均应脱敏"""
    result = mask_sensitive_fields(
        {'reason': 'PASSWORD=x API_KEY=y PassWd=z'}, {'reason'}
    )['reason']

    assert 'PASSWORD=***' in result
    assert 'API_KEY=***' in result
    assert 'PassWd=***' in result


def test_mask_sensitive_fields_extra_sensitive_keys_percent_encoded():
    """新增敏感键同样支持任意单字符百分号编码变体"""
    result = mask_sensitive_fields(
        {'reason': 'pass%77ord=x ap%69_key=y'}, {'reason'}
    )['reason']

    assert 'pass%77ord=***' in result
    assert 'ap%69_key=***' in result
    assert 'pass%77ord=x' not in result
    assert 'ap%69_key=y' not in result


def test_mask_sensitive_fields_extra_sensitive_key_substrings_not_masked():
    """xpassword/authorname 等含新增敏感键名子串的键不应被误伤"""
    original = (
        'xpassword=keep design=keep assign=keep signature=keep '
        'secretary=keep authorname=keep'
    )
    result = mask_sensitive_fields({'reason': original}, {'reason'})['reason']

    assert result == original


def test_mask_sensitive_fields_url_userinfo_and_key_value_combined():
    """同一错误文本中 URL userinfo 与敏感键值对应同时脱敏"""
    result = mask_sensitive_fields(
        {
            'reason': 'ProxyError http://alice:SuperSecret@127.0.0.1:9 '
            'password=MailPass'
        },
        {'reason'},
    )['reason']

    assert 'http://***:***@127.0.0.1:9' in result
    assert 'password=***' in result
    assert 'alice' not in result
    assert 'SuperSecret' not in result
    assert 'MailPass' not in result


def test_double_percent_encoded_sensitive_key_value_is_masked():
    """双层百分号编码（%253D/%255F 解码一层为 %3D/%5F）应脱敏"""
    result = mask_sensitive_fields(
        {'reason': 'access%255Ftoken%253DLEAKED'}, {'reason'}
    )['reason']

    assert 'LEAKED' not in result
    assert '***' in result


def test_double_percent_encoded_sensitive_value_is_masked():
    """双层编码敏感键值中的值应在有限层级解码后脱敏"""
    result = mask_sensitive_fields(
        {'reason': 'access%255Ftoken%253DLEAK%2526sign%253DSIGNED'},
        {'reason'},
    )['reason']

    assert result == 'access%255Ftoken%253D***%2526sign%253D***'


def test_double_percent_encoded_key_char_is_masked():
    """双层编码键名字符（%2577 解码一层为 %77 即 w）应脱敏"""
    result = mask_sensitive_fields(
        {'reason': 'pass%2577ord=LEAK'}, {'reason'}
    )['reason']

    assert 'LEAK' not in result
    assert '***' in result


def test_double_percent_encoded_separator_is_masked():
    """双层编码分隔符（sign%253DSIGNED）应脱敏"""
    result = mask_sensitive_fields(
        {'reason': 'sign%253DSIGNED'}, {'reason'}
    )['reason']

    assert 'SIGNED' not in result
    assert '***' in result


def test_long_double_percent_encoded_sensitive_key_value_is_masked():
    """超过解码上限的文本中双层百分号编码敏感键值仍应脱敏"""
    original = ('x' * 4097) + ' access%255Ftoken%253DLEAKED'

    result = mask_sensitive_fields({'reason': original}, {'reason'})['reason']

    assert 'LEAKED' not in result
    assert result.endswith('access%255Ftoken%253D***')


def test_sensitive_regex_scans_long_text_within_bounded_windows(monkeypatch):
    """长文本脱敏时每次正则扫描均不得超过受控窗口"""
    scanned_lengths = []
    original_regex = utils._SENSITIVE_KEY_VALUE_RE

    class RecordingRegex:
        def finditer(self, value):
            scanned_lengths.append(len(value))
            return original_regex.finditer(value)

        def sub(self, replacement, value):
            scanned_lengths.append(len(value))
            return original_regex.sub(replacement, value)

    monkeypatch.setattr(utils, '_SENSITIVE_KEY_VALUE_RE', RecordingRegex())
    value = ('x' * 100000) + ' access%255Ftoken%253DLEAKED'

    result = mask_sensitive_fields({'reason': value}, {'reason'})['reason']

    assert 'LEAKED' not in result
    assert scanned_lengths
    assert max(scanned_lengths) <= 4096


def test_sensitive_regex_masks_value_when_key_crosses_scan_window(monkeypatch):
    """敏感值跨越扫描窗口时仍应完整脱敏"""
    original_regex = utils._SENSITIVE_KEY_VALUE_RE

    class RecordingRegex:
        def finditer(self, value):
            return original_regex.finditer(value)

        def sub(self, replacement, value):
            return original_regex.sub(replacement, value)

    monkeypatch.setattr(utils, '_SENSITIVE_KEY_VALUE_RE', RecordingRegex())
    prefix = 'x' * 4090
    value = prefix + ' access_token=LEAKED'

    result = mask_sensitive_fields({'reason': value}, {'reason'})['reason']

    assert result.endswith('access_token=***')
    assert 'LEAKED' not in result


def test_long_bare_sensitive_value_crossing_scan_windows_is_fully_masked():
    """超长裸敏感值跨越多个扫描窗口时不得残留后续片段"""
    secret = 'B' * 9000
    original = f'prefix access_token={secret} suffix'

    result = mask_sensitive_fields({'reason': original}, {'reason'})['reason']

    assert result == 'prefix access_token=*** suffix'
    assert secret not in result
    assert 'B' not in result


@pytest.mark.parametrize('quote', ['"', "'"], ids=['double', 'single'])
def test_long_quoted_sensitive_value_crossing_scan_windows_is_fully_masked(
    quote,
):
    """超长单双引号敏感值跨越多个扫描窗口时应脱敏至闭合引号"""
    secret = 'Q' * 9000
    original = f'prefix secret={quote}{secret}{quote} suffix'

    result = mask_sensitive_fields({'reason': original}, {'reason'})['reason']

    assert result == f'prefix secret={quote}***{quote} suffix'
    assert secret not in result
    assert 'Q' not in result


def test_long_url_userinfo_crossing_scan_windows_is_fully_masked():
    """超长 URL userinfo 跨越多个扫描窗口时不得残留用户名或密码"""
    username = 'user' * 1200
    password = 'pass' * 1200
    original = f'ProxyError http://{username}:{password}@example.com:8080/path'

    result = mask_sensitive_fields({'reason': original}, {'reason'})['reason']

    assert result == 'ProxyError http://***:***@example.com:8080/path'
    assert username not in result
    assert password not in result


def test_percent_encoded_text_keeps_url_without_userinfo_unchanged():
    """含百分号编码的文本不得将普通 URL 误判为未完成 userinfo"""
    original = 'prefix%20 redirect=https://example.com'

    result = mask_sensitive_fields({'reason': original}, {'reason'})['reason']

    assert result == original


def test_cross_window_masking_keeps_all_regex_scans_bounded(monkeypatch):
    """跨窗口完整脱敏时各敏感正则的单次扫描仍不得超过边界"""
    scanned_lengths = []

    class RecordingRegex:
        def __init__(self, pattern):
            self.pattern = pattern

        def finditer(self, value):
            scanned_lengths.append(len(value))
            return self.pattern.finditer(value)

        def sub(self, replacement, value):
            scanned_lengths.append(len(value))
            return self.pattern.sub(replacement, value)

    monkeypatch.setattr(
        utils,
        '_SENSITIVE_KEY_VALUE_RE',
        RecordingRegex(utils._SENSITIVE_KEY_VALUE_RE),
    )
    monkeypatch.setattr(
        utils,
        '_URL_USERINFO_RE',
        RecordingRegex(utils._URL_USERINFO_RE),
    )
    secret = 'S' * 9000
    userinfo = 'U' * 9000
    value = f'access_token={secret} http://{userinfo}:password@example.com'

    result = mask_sensitive_fields({'reason': value}, {'reason'})['reason']

    assert result == 'access_token=*** http://***:***@example.com'
    assert scanned_lengths
    assert max(scanned_lengths) <= 4096


def test_single_layer_percent_encoding_keeps_original_encoding_form():
    """单层编码防回归：保留编码形式、只替换值"""
    result = mask_sensitive_fields(
        {'reason': 'access%5Ftoken%3DLEAKED'}, {'reason'}
    )['reason']

    assert result == 'access%5Ftoken%3D***'
    assert 'LEAKED' not in result


def test_plain_sensitive_value_still_masked_as_before():
    """明文防回归：access_token=LEAK 应脱敏为 access_token=***"""
    result = mask_sensitive_fields(
        {'reason': 'access_token=LEAK'}, {'reason'}
    )['reason']

    assert result == 'access_token=***'
    assert 'LEAK' not in result


def test_invalid_percent_sequences_do_not_raise_or_mask():
    """非法百分号序列（%ZZ、孤立 %、token=%）不抛异常且不误伤"""
    result = mask_sensitive_fields(
        {'reason': 'percent=%ZZ lone=% token=%'}, {'reason'}
    )['reason']

    assert result == 'percent=%ZZ lone=% token=***'


def test_non_sensitive_double_encoded_text_not_masked():
    """非敏感内容的双层编码文本（a%253Db=keep、普通%25文本）不误伤"""
    result = mask_sensitive_fields(
        {'reason': 'a%253Db=keep 普通%25文本'}, {'reason'}
    )['reason']

    assert result == 'a%253Db=keep 普通%25文本'


@pytest.mark.parametrize(
    'time_str',
    ['infs', '1e400s', '-infs', 'nans'],
    ids=['inf', 'overflow', 'negative_inf', 'nan'],
)
def test_parse_time_string_rejects_non_finite_values(time_str):
    """非有限时间数值（inf / -inf / nan）应抛出 ValueError"""
    with pytest.raises(ValueError):
        parse_time_string(time_str)


@pytest.mark.parametrize(
    'time_str',
    ['infh', 'infm', 'infs', 'nanh', 'nanm', 'nans'],
)
def test_parse_time_string_rejects_non_finite_values_for_all_units(time_str):
    """h/m/s 三种单位下的非有限数值均应抛出 ValueError"""
    with pytest.raises(ValueError):
        parse_time_string(time_str)


@pytest.mark.parametrize(
    ('time_str', 'expected'),
    [('3s', 3), ('1h', 3600), ('15m', 900), ('1.5s', 1.5)],
)
def test_parse_time_string_keeps_valid_values(time_str, expected):
    """防回归：合法时间字符串的解析结果保持不变"""
    assert parse_time_string(time_str) == expected


@pytest.mark.parametrize('time_str', ['infs', '1e400s', '-infs'])
def test_parse_time_string_int_conversion_raises_value_error(time_str):
    """调用方 int(parse_time_string(...)) 形态须抛 ValueError 而非 OverflowError"""
    with pytest.raises(ValueError):
        int(parse_time_string(time_str))


@pytest.mark.parametrize(
    'encoded',
    ['%2C', '%26', '%22', '%27', '%7D', '%5D'],
    ids=['comma', 'ampersand', 'double_quote', 'single_quote',
         'right_brace', 'right_bracket'],
)
def test_encoded_terminator_inside_sensitive_value_is_fully_masked(encoded):
    """敏感值内编码的结构性终止字符不得截断脱敏，值须整段替换"""
    result = mask_sensitive_fields(
        {'reason': f'access_token=abc{encoded}def'}, {'reason'}
    )['reason']

    assert result == 'access_token=***'
    assert 'def' not in result


def test_encoded_quote_wrapped_sensitive_value_is_fully_masked():
    """编码引号包裹的敏感值应整段脱敏，不残留编码引号也不错乱结构"""
    result = mask_sensitive_fields(
        {'reason': 'access_token=%22abc%22'}, {'reason'}
    )['reason']

    assert result == 'access_token=***'


def test_sensitive_value_starting_with_encoded_terminator_is_masked():
    """敏感值以编码终止字符开头（token=%2Cabc）时仍须脱敏，不得整段漏过"""
    result = mask_sensitive_fields({'reason': 'token=%2Cabc'}, {'reason'})['reason']

    assert result == 'token=***'


def test_double_encoded_terminator_inside_sensitive_value_is_masked():
    """双层编码终止字符（token=a%252Cb）解码后不得残留值尾部字符"""
    result = mask_sensitive_fields({'reason': 'token=a%252Cb'}, {'reason'})['reason']

    assert 'b' not in result
    assert '***' in result


def test_encoded_terminator_value_keeps_non_sensitive_query_param():
    """值内编码终止字符整段脱敏的同时，& 后的非敏感参数须原样保留"""
    result = mask_sensitive_fields(
        {'reason': 'access_token=abc%2Cdef&name=value'}, {'reason'}
    )['reason']

    assert result == 'access_token=***&name=value'


def test_overlapping_decoded_matches_do_not_produce_malformed_mask():
    """解码路径结尾扫描造成区间重叠时不得重复写入产生畸形星号"""
    result = mask_sensitive_fields(
        {'reason': 'token=a&%2Csign=b'}, {'reason'}
    )['reason']

    assert result == 'token=***'
    assert '*****' not in result


@pytest.mark.parametrize(
    'suffix',
    [
        'access_token=abc%2Cdef',
        'access_token=abc%26def',
        'access_token=%22abc%22',
        'token=%2Cabc',
        'access_token=abc%2Cdef&name=value',
    ],
    ids=['comma', 'ampersand', 'quote_wrapped', 'leading_comma', 'query_param'],
)
def test_decoded_and_raw_paths_mask_consistently(suffix):
    """超过解码上限走原文路径时，尾部脱敏结果须与短输入解码路径一致"""
    prefix = ('x' * 4097) + ' '
    short_result = mask_sensitive_fields({'reason': suffix}, {'reason'})['reason']
    long_result = mask_sensitive_fields(
        {'reason': prefix + suffix}, {'reason'}
    )['reason']

    assert long_result == prefix + short_result
