#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""测试包公共工具

为避免仓库中出现任何完整手机号（即使是虚拟号码），需要手机号样本的测试
统一从本模块获取运行时随机生成的号码，源码内不保留完整号码字面量，也不
通过字符串拼接固定号码的方式绕过该约束。
"""

import random
import string

MOBILE_LENGTH = 11
MOBILE_FIRST_DIGIT = '1'
MOBILE_SECOND_DIGITS = '3456789'


def make_mobile_number(exclude=()):
    """生成随机的中国大陆手机号样本。

    号码首位固定为 1，第二位取自 3-9 号段，其余位随机，整体长度为 11 位，
    满足程序对手机号的识别与脱敏规则。

    Args:
        exclude: 需要避开的号码集合，用于生成互不相同的多个样本

    Returns:
        str: 11 位手机号样本
    """
    excluded = set(exclude)
    while True:
        digits = [MOBILE_FIRST_DIGIT, random.choice(MOBILE_SECOND_DIGITS)]
        digits.extend(
            random.choice(string.digits) for _ in range(MOBILE_LENGTH - 2)
        )
        mobile = ''.join(digits)
        if mobile not in excluded:
            return mobile


def mask_mobile_number(mobile):
    """计算手机号样本经脱敏后的期望文本。

    Args:
        mobile: 11 位手机号样本

    Returns:
        str: 保留前 3 位与后 4 位、中间以 4 个星号替代的脱敏文本
    """
    return f'{mobile[:3]}****{mobile[-4:]}'
