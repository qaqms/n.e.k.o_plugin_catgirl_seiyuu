# 猫娘声优

让猫娘用她**本人的声音**逐字朗读剧情游戏的台词：外部截屏 + OCR 读字（不触碰
游戏进程，零封号风险），台词经稳定窗口/去重/UI 词/speaker 规则判定后，走宿主
游戏 SDK B 层官方语音出口（`POST /api/game/catgirl_seiyuu/speak`）逐句播报，
一句播完再播下一句。对她说「打开配音模式」即可开始，你在聊天里说话会自动
暂停让位。

- 控制：面板按钮 / `@llm_tool`（start/stop/pause/status）/ 手动投喂一行直接朗读
- 判定：连续 N 帧文本稳定才播（抗打字机半句）；最近 64 条 hash 去重（防翻页重播）；
  主角台词（`dub_protagonist`）与内心独白（`dub_monologue`）可分别开关
- 面板：目标窗口选择、预览图上拖拽框选对话框区域、规则微调、「跳过的行」纠错列表
- 护栏：显式指定的目标窗口失效时报错而不是静默换窗；语音默认跟随前台时
  拒绝 N.E.K.O 自身窗口（防「朗读自己的聊天记录」回声回路，可关）
- 平台：v0.1 仅 Windows 桌面截屏（DirectX 独占全屏会截到黑帧，请窗口化）

配置见 `plugin.toml`（出厂默认）与运行时 `config/plugin.toml`；上手教程见
`docs/quickstart.md`。v2+ 计划（Textractor hook、多角色声线）见
`DESIGN_BRIEF_catgirl_seiyuu.md`。

## Development

This directory is both the editable plugin source and its Git repository.

当前目录既是可编辑的插件源码，也是插件自己的 Git 仓库。

このディレクトリは、編集するプラグインソースであり、プラグイン自身の Git リポジトリでもあります。

When publishing to the plugin market, use this GitHub repository name:

发布到插件市场时，请使用以下 GitHub 仓库名：

プラグインマーケットへ公開する際は、次の GitHub リポジトリ名を使用してください：

```text
n.e.k.o_plugin_catgirl_seiyuu
```

From this plugin repository root:

```bash
uvx ruff==0.12.4 check --ignore-noqa --config ruff.toml .
```

From this plugin repository root / 在当前插件仓库根目录中 / このプラグインリポジトリのルートで：

```bash
uv run --with pip --project "../N.E.K.O" neko-plugin sync . --clean
uv run --project "../N.E.K.O" neko-plugin check .
uv run --project "../N.E.K.O" neko-plugin check -r .
```

Python runtime dependencies are declared in `pyproject.toml` and synced into
`vendor/` for packaging. The generated `vendor/` directory is not committed;
local builds and CI recreate it before release checks.

Python 运行时依赖声明在 `pyproject.toml` 中，并在打包时同步到 `vendor/`。
生成的 `vendor/` 不提交；本地构建和 CI 会在发布检查前重新生成它。

Python ランタイム依存関係は `pyproject.toml` に宣言し、パッケージ化時に
`vendor/` へ同期します。生成された `vendor/` はコミットせず、ローカルビルドと
CI が公開前チェックで再生成します。

## Market release / Market 发布 / Market 公開

Publish the version declared in `plugin.toml`. By default this pushes the Git
tag, waits for the standard GitHub Release, and notifies the plugin market.

发布 `plugin.toml` 中声明的版本。默认会推送 Git tag、等待标准 GitHub
Release，然后通知插件市场。

`plugin.toml` で宣言されたバージョンを公開します。既定では Git tag を
push し、標準 GitHub Release を待ってからプラグインマーケットへ通知します。

```bash
uv run --project "../N.E.K.O" neko-plugin publish .
```

To run only one half explicitly / 如需仅执行一部分 / 一方のみを実行する場合:

```bash
uv run --project "../N.E.K.O" neko-plugin publish github .
uv run --project "../N.E.K.O" neko-plugin publish market https://github.com/owner/repo/releases/tag/v0.1.0
```

The generated `.github/workflows/release.yml` builds and uploads
`catgirl_seiyuu.neko-plugin`. The market independently verifies that Release
before publishing it.

生成的 `.github/workflows/release.yml` 会构建并上传插件包；Market 会独立验证
该 Release 后再发布。

生成された `.github/workflows/release.yml` がプラグインパッケージをビルドして
アップロードし、Market はその Release を独立検証してから公開します。

## Entry

```toml
entry = "plugin.plugins.catgirl_seiyuu:CatgirlSeiyuuPlugin"
```
