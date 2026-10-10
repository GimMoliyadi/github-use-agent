# 模拟研究报告（真实仓库代码证据）

**模拟模式（Mock）：研究策略与文字由确定性模拟模型生成。**

研究目标：研究签名和验证的实现：定位 Signer.sign、Signer.unsign 与 Serializer.loads 的关键代码入口和静态调用关系。

仓库：pallets/itsdangerous  
固定 Commit：`672971d66a2ef9f85151e53283113f33d642dabd`

## 模型摘要（未逐项验证）

\[MOCK\] 本报告由确定性模拟模型生成，未调用第三方 LLM。探索使用真实工具结果；下列引用仍需由应用逐项验证。研究问题：研究签名和验证的实现：定位 Signer.sign、Signer.unsign 与 Serializer.loads 的关键代码入口和静态调用关系。

技术结论以以下带来源的发现为准。

## 模拟探索取得的真实证据

- **有引用的事实**：\[MOCK\] \`src/itsdangerous/encoding.py\` 的引用片段包含符号声明文本：\`want\_bytes\`。 来源：[src/itsdangerous/encoding.py:L11-L11](https://github.com/pallets/itsdangerous/blob/672971d66a2ef9f85151e53283113f33d642dabd/src/itsdangerous/encoding.py#L11)
- **有引用的事实**：\[MOCK\] \`src/itsdangerous/encoding.py\` 的引用片段包含符号声明文本：\`base64\_encode\`。 来源：[src/itsdangerous/encoding.py:L20-L20](https://github.com/pallets/itsdangerous/blob/672971d66a2ef9f85151e53283113f33d642dabd/src/itsdangerous/encoding.py#L20)
- **有引用的事实**：\[MOCK\] \`src/itsdangerous/encoding.py\` 的引用片段包含符号声明文本：\`base64\_decode\`。 来源：[src/itsdangerous/encoding.py:L28-L28](https://github.com/pallets/itsdangerous/blob/672971d66a2ef9f85151e53283113f33d642dabd/src/itsdangerous/encoding.py#L28)
- **有引用的事实**：\[MOCK\] \`src/itsdangerous/encoding.py\` 的引用片段包含符号声明文本：\`int\_to\_bytes\`。 来源：[src/itsdangerous/encoding.py:L49-L49](https://github.com/pallets/itsdangerous/blob/672971d66a2ef9f85151e53283113f33d642dabd/src/itsdangerous/encoding.py#L49)
- **有引用的事实**：\[MOCK\] \`src/itsdangerous/signer.py\` 的引用片段包含符号声明文本：\`Signer\`、\`constructor\`、\`\_\_init\_\_\`、\`secret\_key\`、\`derive\_key\`、\`get\_signature\`、\`sign\`。 来源：[src/itsdangerous/signer.py:L72-L226](https://github.com/pallets/itsdangerous/blob/672971d66a2ef9f85151e53283113f33d642dabd/src/itsdangerous/signer.py#L72-L226)
- **有引用的事实**：\[MOCK\] \`src/itsdangerous/signer.py\` 的引用片段包含符号声明文本：\`\_\_init\_\_\`。 来源：[src/itsdangerous/signer.py:L52-L60](https://github.com/pallets/itsdangerous/blob/672971d66a2ef9f85151e53283113f33d642dabd/src/itsdangerous/signer.py#L52-L60)
- **有引用的事实**：\[MOCK\] \`tests/test\_itsdangerous/test\_signer.py\` 的引用片段包含符号声明文本：\`\_ReverseAlgorithm\`、\`get\_signature\`、\`TestSigner\`、\`signer\_factory\`、\`signer\`、\`test\_signer\`、\`test\_no\_separator\`、\`test\_broken\_signature\`。 来源：[tests/test\_itsdangerous/test\_signer.py:L1-L109](https://github.com/pallets/itsdangerous/blob/672971d66a2ef9f85151e53283113f33d642dabd/tests/test_itsdangerous/test_signer.py#L1-L109)

## 未确认事项

- **尚未确认**：\[MOCK\] 模拟决策器不能确认完整实现机制、运行时行为或架构设计意图；这些结论需要配置真实模型后进一步研究。（证据不足）

## 代码证据

### [src/itsdangerous/encoding.py，L11–L11](https://github.com/pallets/itsdangerous/blob/672971d66a2ef9f85151e53283113f33d642dabd/src/itsdangerous/encoding.py#L11)

来源类型：code；Blob：`f5ca80f905c9bb29b993c437a89174e668704658`

```python
def want_bytes(
```

### [src/itsdangerous/encoding.py，L20–L20](https://github.com/pallets/itsdangerous/blob/672971d66a2ef9f85151e53283113f33d642dabd/src/itsdangerous/encoding.py#L20)

来源类型：code；Blob：`f5ca80f905c9bb29b993c437a89174e668704658`

```python
def base64_encode(string: str | bytes) -> bytes:
```

### [src/itsdangerous/encoding.py，L28–L28](https://github.com/pallets/itsdangerous/blob/672971d66a2ef9f85151e53283113f33d642dabd/src/itsdangerous/encoding.py#L28)

来源类型：code；Blob：`f5ca80f905c9bb29b993c437a89174e668704658`

```python
def base64_decode(string: str | bytes) -> bytes:
```

### [src/itsdangerous/encoding.py，L49–L49](https://github.com/pallets/itsdangerous/blob/672971d66a2ef9f85151e53283113f33d642dabd/src/itsdangerous/encoding.py#L49)

来源类型：code；Blob：`f5ca80f905c9bb29b993c437a89174e668704658`

```python
def int_to_bytes(num: int) -> bytes:
```

### [src/itsdangerous/signer.py，L72–L226](https://github.com/pallets/itsdangerous/blob/672971d66a2ef9f85151e53283113f33d642dabd/src/itsdangerous/signer.py#L72-L226)

来源类型：code；Blob：`e324dc03da90d9002200b68088f501df62777cd6`

```python

    return [want_bytes(s) for s in secret_key]  # pyright: ignore


class Signer:
    """A signer securely signs bytes, then unsigns them to verify that
    the value hasn't been changed.

    The secret key should be a random string of ``bytes`` and should not
    be saved to code or version control. Different salts should be used
    to distinguish signing in different contexts. See :doc:`/concepts`
    for information about the security of the secret key and salt.

    :param secret_key: The secret key to sign and verify with. Can be a
        list of keys, oldest to newest, to support key rotation.
    :param salt: Extra key to combine with ``secret_key`` to distinguish
        signatures in different contexts.
    :param sep: Separator between the signature and value.
    :param key_derivation: How to derive the signing key from the secret
        key and salt. Possible values are ``concat``, ``django-concat``,
        or ``hmac``. Defaults to :attr:`default_key_derivation`, which
        defaults to ``django-concat``.
    :param digest_method: Hash function to use when generating the HMAC
        signature. Defaults to :attr:`default_digest_method`, which
        defaults to :func:`hashlib.sha1`. Note that the security of the
        hash alone doesn't apply when used intermediately in HMAC.
    :param algorithm: A :class:`SigningAlgorithm` instance to use
        instead of building a default :class:`HMACAlgorithm` with the
        ``digest_method``.

    .. versionchanged:: 2.0
        Added support for key rotation by passing a list to
        ``secret_key``.

    .. versionchanged:: 0.18
        ``algorithm`` was added as an argument to the class constructor.

    .. versionchanged:: 0.14
        ``key_derivation`` and ``digest_method`` were added as arguments
        to the class constructor.
    """

    #: The default digest method to use for the signer. The default is
    #: :func:`hashlib.sha1`, but can be changed to any :mod:`hashlib` or
    #: compatible object. Note that the security of the hash alone
    #: doesn't apply when used intermediately in HMAC.
    #:
    #: .. versionadded:: 0.14
    default_digest_method: t.Any = staticmethod(_lazy_sha1)

    #: The default scheme to use to derive the signing key from the
    #: secret key and salt. The default is ``django-concat``. Possible
    #: values are ``concat``, ``django-concat``, and ``hmac``.
    #:
    #: .. versionadded:: 0.14
    default_key_derivation: str = "django-concat"

    def __init__(
        self,
        secret_key: str | bytes | cabc.Iterable[str] | cabc.Iterable[bytes],
        salt: str | bytes | None = b"itsdangerous.Signer",
        sep: str | bytes = b".",
        key_derivation: str | None = None,
        digest_method: t.Any | None = None,
        algorithm: SigningAlgorithm | None = None,
    ):
        #: The list of secret keys to try for verifying signatures, from
        #: oldest to newest. The newest (last) key is used for signing.
        #:
        #: This allows a key rotation system to keep a list of allowed
        #: keys and remove expired ones.
        self.secret_keys: list[bytes] = _make_keys_list(secret_key)
        self.sep: bytes = want_bytes(sep)

        if self.sep in _base64_alphabet:
            raise ValueError(
                "The given separator cannot be used because it may be"
                " contained in the signature itself. ASCII letters,"
                " digits, and '-_=' must not be used."
            )

        if salt is not None:
            salt = want_bytes(salt)
        else:
            salt = b"itsdangerous.Signer"

        self.salt = salt

        if key_derivation is None:
            key_derivation = self.default_key_derivation

        self.key_derivation: str = key_derivation

        if digest_method is None:
            digest_method = self.default_digest_method

        self.digest_method: t.Any = digest_method

        if algorithm is None:
            algorithm = HMACAlgorithm(self.digest_method)

        self.algorithm: SigningAlgorithm = algorithm

    @property
    def secret_key(self) -> bytes:
        """The newest (last) entry in the :attr:`secret_keys` list. This
        is for compatibility from before key rotation support was added.
        """
        return self.secret_keys[-1]

    def derive_key(self, secret_key: str | bytes | None = None) -> bytes:
        """This method is called to derive the key. The default key
        derivation choices can be overridden here. Key derivation is not
        intended to be used as a security method to make a complex key
        out of a short password. Instead you should use large random
        secret keys.

        :param secret_key: A specific secret key to derive from.
            Defaults to the last item in :attr:`secret_keys`.

        .. versionchanged:: 2.0
            Added the ``secret_key`` parameter.
        """
        if secret_key is None:
            secret_key = self.secret_keys[-1]
        else:
            secret_key = want_bytes(secret_key)

        if self.key_derivation == "concat":
            return t.cast(bytes, self.digest_method(self.salt + secret_key).digest())
        elif self.key_derivation == "django-concat":
            return t.cast(
                bytes, self.digest_method(self.salt + b"signer" + secret_key).digest()
            )
        elif self.key_derivation == "hmac":
            mac = hmac.new(secret_key, digestmod=self.digest_method)
            mac.update(self.salt)
            return mac.digest()
        elif self.key_derivation == "none":
            return secret_key
        else:
            raise TypeError("Unknown key derivation method")

    def get_signature(self, value: str | bytes) -> bytes:
        """Returns the signature for the given value."""
        value = want_bytes(value)
        key = self.derive_key()
        sig = self.algorithm.get_signature(key, value)
        return base64_encode(sig)

    def sign(self, value: str | bytes) -> bytes:
        """Signs the given string."""
        value = want_bytes(value)
        return value + self.sep + self.get_signature(value)

```

### [src/itsdangerous/signer.py，L52–L60](https://github.com/pallets/itsdangerous/blob/672971d66a2ef9f85151e53283113f33d642dabd/src/itsdangerous/signer.py#L52-L60)

来源类型：code；Blob：`e324dc03da90d9002200b68088f501df62777cd6`

```python
    #: SHA1, but can be changed to any other function in the hashlib
    #: module.
    default_digest_method: t.Any = staticmethod(_lazy_sha1)

    def __init__(self, digest_method: t.Any = None):
        if digest_method is None:
            digest_method = self.default_digest_method

        self.digest_method: t.Any = digest_method
```

### [tests/test\_itsdangerous/test\_signer.py，L1–L109](https://github.com/pallets/itsdangerous/blob/672971d66a2ef9f85151e53283113f33d642dabd/tests/test_itsdangerous/test_signer.py#L1-L109)

来源类型：code；Blob：`1e053883aad5b517cb3acf3f7c46d2561bb33b96`

```python
import hashlib
from functools import partial

import pytest

from itsdangerous.exc import BadSignature
from itsdangerous.signer import HMACAlgorithm
from itsdangerous.signer import NoneAlgorithm
from itsdangerous.signer import Signer
from itsdangerous.signer import SigningAlgorithm


class _ReverseAlgorithm(SigningAlgorithm):
    def get_signature(self, key, value):
        return (key + value)[::-1]


class TestSigner:
    @pytest.fixture()
    def signer_factory(self):
        return partial(Signer, secret_key="secret-key")

    @pytest.fixture()
    def signer(self, signer_factory):
        return signer_factory()

    def test_signer(self, signer):
        signed = signer.sign("my string")
        assert isinstance(signed, bytes)
        assert signer.validate(signed)
        out = signer.unsign(signed)
        assert out == b"my string"

    def test_no_separator(self, signer):
        signed = signer.sign("my string")
        signed = signed.replace(signer.sep, b"*", 1)
        assert not signer.validate(signed)

        with pytest.raises(BadSignature):
            signer.unsign(signed)

    def test_broken_signature(self, signer):
        signed = signer.sign("b")
        bad_signed = signed[:-1]
        bad_sig = bad_signed.rsplit(b".", 1)[1]
        assert not signer.verify_signature(b"b", bad_sig)

        with pytest.raises(BadSignature) as exc_info:
            signer.unsign(bad_signed)

        assert exc_info.value.payload == b"b"

    def test_changed_value(self, signer):
        signed = signer.sign("my string")
        signed = signed.replace(b"my", b"other", 1)
        assert not signer.validate(signed)

        with pytest.raises(BadSignature):
            signer.unsign(signed)

    def test_invalid_separator(self, signer_factory):
        with pytest.raises(ValueError) as exc_info:
            signer_factory(sep="-")

        assert "separator cannot be used" in str(exc_info.value)

    @pytest.mark.parametrize(
        "key_derivation", ("concat", "django-concat", "hmac", "none")
    )
    def test_key_derivation(self, signer_factory, key_derivation):
        signer = signer_factory(key_derivation=key_derivation)
        assert signer.unsign(signer.sign("value")) == b"value"

    def test_invalid_key_derivation(self, signer_factory):
        signer = signer_factory(key_derivation="invalid")

        with pytest.raises(TypeError):
            signer.derive_key()

    def test_digest_method(self, signer_factory):
        signer = signer_factory(digest_method=hashlib.md5)
        assert signer.unsign(signer.sign("value")) == b"value"

    @pytest.mark.parametrize(
        "algorithm", (None, NoneAlgorithm(), HMACAlgorithm(), _ReverseAlgorithm())
    )
    def test_algorithm(self, signer_factory, algorithm):
        signer = signer_factory(algorithm=algorithm)
        assert signer.unsign(signer.sign("value")) == b"value"

        if algorithm is None:
            assert signer.algorithm.digest_method == signer.digest_method

    def test_secret_keys(self):
        signer = Signer("a")
        signed = signer.sign("my string")
        assert isinstance(signed, bytes)

        signer = Signer(["a", "b"])
        assert signer.validate(signed)
        out = signer.unsign(signed)
        assert out == b"my string"


def test_abstract_algorithm():
    alg = SigningAlgorithm()

    with pytest.raises(NotImplementedError):
        alg.get_signature(b"a", b"b")
```

## 限制与未确认事项

- 静态分析不会执行仓库代码；跨文件候选和调用语法不能确认动态分派或运行时行为。
- 仅分析已获取的 3 个文件；预算内的抽样搜索不代表完整仓库覆盖。
- 引用校验确认固定 Commit 下的文件、字节、行号和原文；自然语言结论仍需人工判断证据是否充分。
- 本报告使用 Mock 模型生成研究决策与草稿；代码证据来自配置的数据源。
- 未配置或未获得可核对的模型价格信息，实际美元费用未知。
- 研究停止原因：model\_sufficient。
- \[MOCK\] 没有真实 LLM 推理；模拟报告用于验证工程流程和引用准确性。
- 仅进行有限静态探索；静态引用不保证运行时执行，未执行目标仓库代码。
- 未读文件、被截断片段和未验证机制不在确认范围内。

## 资源使用

- 探索轮数：10；文件：3；分析字节：14491。
- GitHub 请求：6；缓存命中：0；模型调用：0。
- 模型 Token：输入 0，输出 0。
- 实际模型费用：未知。
