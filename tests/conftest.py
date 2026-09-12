"""独立仓库的测试基建：桩掉宿主 SDK，把插件根目录加载为包 ``catgirl_seiyuu``。

模式与 forever_companion / your_memory 的 conftest 同源（该骨架已在官方市场
CI 双环境验证过）：

1. 往 ``sys.modules`` 注入最小可用的 ``plugin.sdk.plugin`` 桩（装饰器原样返回
   函数/类，``tr()`` 返回默认文案）；
2. 用 importlib 把根目录 ``__init__.py`` 注册为包 ``catgirl_seiyuu``，使
   ``from .core.rules import ...`` 等相对导入正常工作；
3. 挂载模式防护：预注册外层父包轻量桩，阻止 pytest 在宿主包树内真导入重型
   ``plugin/__init__.py``。

services.capture / services.ocr 的重依赖（PIL / _shared.rapidocr）都是运行时
惰性导入，测试环境无需安装。
"""

import importlib.util
import inspect
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def pytest_pyfunc_call(pyfuncitem):
    """async def test_* 直接跑：不引入 pytest-asyncio（与 fc 同纪律）。"""

    fn = pyfuncitem.obj
    if inspect.iscoroutinefunction(fn):
        kwargs = {name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
        import asyncio

        asyncio.run(fn(**kwargs))
        return True
    return None


def _pre_register_parent_packages() -> None:
    chain: list[tuple[str, Path]] = []
    current = ROOT
    while True:
        init_file = current / "__init__.py"
        if not init_file.is_file():
            break
        chain.append((current.name, current))
        parent = current.parent
        if parent == current:
            break
        current = parent
    if len(chain) < 2:
        return
    chain.reverse()
    dotted_parts: list[str] = []
    for name, directory in chain:
        dotted_parts.append(name)
        dotted = ".".join(dotted_parts)
        if dotted in sys.modules:
            continue
        stub = types.ModuleType(dotted)
        stub.__file__ = str(directory / "__init__.py")
        stub.__path__ = [str(directory)]
        sys.modules[dotted] = stub


_pre_register_parent_packages()


def pytest_collect_directory(path, parent):
    """强制所有目录按 Dir 收集，不建 Package 节点（根 __init__.py 是包本体）。"""

    from _pytest.nodes import Dir

    return Dir.from_parent(parent, path=path)


# ---------------------------------------------------------------------------
# SDK 桩
# ---------------------------------------------------------------------------


class Ok:
    def __init__(self, value=None):
        self.value = value

    def __repr__(self) -> str:
        return f"Ok({self.value!r})"


class Err:
    def __init__(self, error=None):
        self.error = error

    def __repr__(self) -> str:
        return f"Err({self.error!r})"


class SdkError(Exception):
    pass


def _identity_decorator_factory(**kwargs):
    def deco(fn):
        return fn

    return deco


def _identity_decorator(target):
    return target


def _unwrap_or(result, default=None):
    if isinstance(result, Err):
        return default
    return getattr(result, "value", result)


def unwrap(value):
    if isinstance(value, Err):
        raise ValueError(value.error)
    return getattr(value, "value", value)


def _make_sdk_module() -> types.ModuleType:
    plugin_pkg = types.ModuleType("plugin")
    sdk_pkg = types.ModuleType("plugin.sdk")
    mod = types.ModuleType("plugin.sdk.plugin")

    mod.Ok = Ok
    mod.Err = Err
    mod.SdkError = SdkError
    mod.Result = object
    mod.tr = lambda key, *, default=None, **_: default if default is not None else key
    mod.unwrap_or = _unwrap_or
    mod.unwrap = unwrap

    class NekoPluginBase:
        def __init__(self, ctx):
            self.ctx = ctx

        @property
        def plugin_id(self) -> str:
            return str(getattr(self.ctx, "plugin_id", "plugin"))

    mod.NekoPluginBase = NekoPluginBase
    def _message_strict(*, id, name=None, description="", input_schema=None, source=None, metadata=None):
        """与宿主 plugin.sdk.plugin.message 同签名：参数漂移（如已删除的
        auto_start）在测试期即 TypeError，不等打包元数据探测才暴雷。"""

        return _identity_decorator_factory(id=id, source=source)

    mod.neko_plugin = _identity_decorator
    mod.lifecycle = _identity_decorator_factory
    mod.timer_interval = _identity_decorator_factory
    mod.llm_tool = _identity_decorator_factory
    mod.plugin_entry = _identity_decorator_factory
    mod.message = _message_strict
    mod.quick_action = _identity_decorator_factory

    class _UiNamespace:
        @staticmethod
        def context(**kwargs):
            def deco(fn):
                fn._ui_context_id = kwargs.get("id")
                return fn

            return deco

        @staticmethod
        def action(**kwargs):
            def deco(fn):
                fn._ui_action_meta = kwargs
                return fn

            return deco

    mod.ui = _UiNamespace

    plugin_pkg.sdk = sdk_pkg
    sdk_pkg.plugin = mod

    sys.modules["plugin"] = plugin_pkg
    sys.modules["plugin.sdk"] = sdk_pkg
    sys.modules["plugin.sdk.plugin"] = mod
    return mod


if "plugin.sdk.plugin" not in sys.modules:
    _make_sdk_module()


# ---------------------------------------------------------------------------
# 把插件根目录加载为包 ``catgirl_seiyuu``
# ---------------------------------------------------------------------------


def _load_package() -> types.ModuleType:
    if "catgirl_seiyuu" in sys.modules:
        return sys.modules["catgirl_seiyuu"]
    spec = importlib.util.spec_from_file_location(
        "catgirl_seiyuu", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [str(ROOT)]
    module.__package__ = "catgirl_seiyuu"
    sys.modules["catgirl_seiyuu"] = module
    sys.modules.setdefault("__init__", module)
    spec.loader.exec_module(module)
    return module


catgirl_seiyuu = _load_package()


# ---------------------------------------------------------------------------
# 假宿主件与插件工厂
# ---------------------------------------------------------------------------


class FakeLogger:
    def _emit(self, *args, **kwargs):
        pass

    info = warning = error = debug = exception = _emit


class FakeStore:
    def __init__(self):
        self.data: dict = {}

    async def set(self, key, value):
        self.data[key] = value
        return Ok(True)

    async def get(self, key):
        return Ok(self.data.get(key))


class FakeConfig:
    def __init__(self, initial=None):
        self.data = dict(initial or {})
        self.updates: list = []

    async def get(self, path, default=None, **_):
        cur = self.data
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    async def update(self, patch, **_):
        self.updates.append(patch)
        for section, values in patch.items():
            if isinstance(values, dict):
                merged = dict(self.data.get(section) or {})
                merged.update(values)
                self.data[section] = merged
            else:
                self.data[section] = values
        return self.data


class FakeCtx:
    plugin_id = "catgirl_seiyuu"
    logger = FakeLogger()
    metadata: dict = {}

    def push_message(self, **kwargs):
        self.pushed = getattr(self, "pushed", [])
        self.pushed.append(kwargs)
        return {"submitted": True}


def make_plugin(**config):
    """构造挂好假 store/config 的插件实例（不跑 startup 生命周期）。"""

    plugin = catgirl_seiyuu.CatgirlSeiyuuPlugin(FakeCtx())
    plugin.store = FakeStore()
    plugin.config = FakeConfig(config)
    plugin.metadata = {}
    plugin.push_message = lambda **kw: FakeCtx.push_message(FakeCtx(), **kw)
    return plugin


@pytest.fixture()
def cs_make_plugin():
    return make_plugin


@pytest.fixture()
def cs_ok():
    return Ok


@pytest.fixture()
def cs_err():
    return Err
