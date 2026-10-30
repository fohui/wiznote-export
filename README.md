# 为知笔记导出工具

将 macOS 为知笔记 App 中已同步的笔记导出为 Markdown，并下载图片和附件。导出时保留原有文件夹层级，可将输出目录放入 Obsidian 仓库。工具只读取本机 `index.db`，不会修改为知笔记数据。

> Agent 导航：按「安装依赖 → 检查参数 → 完整导出 → 核对结果」的顺序操作。`--username` 和 `--output` 必须由使用者指定；密码只在终端提示时输入，不写入命令或文件。排查失败时先读取输出目录中的 `export-report.json`。

## 使用条件

- macOS 已安装为知笔记 App，并登录、同步过待导出的账号。本机索引用于获取笔记清单；正文、图片和附件仍需联网下载。
- Python 3.9 或更新版本；账号可以通过密码登录为知笔记官方服务 `as.wiz.cn`。
- 暂不支持需要 WebSocket 获取正文的协作笔记、第三方账号登录和自建为知服务器。

## 1. 安装依赖

推荐在项目根目录安装 CLI。`uv` 会安装 `pyproject.toml` 中声明的 `beautifulsoup4` 和 `markdownify`：

```bash
uv tool install .
```

没有 `uv` 时，可使用 Python 虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

## 2. 检查参数

```bash
wiznote-export --help
```

`--username` 是为知笔记登录账号，`--output` 是导出目录，两者必填。`--db` 是可选的本机 `index.db` 路径，仅在自动定位索引失败时使用。

## 3. 完整导出

将示例账号和目录替换为自己的值后运行：

```bash
wiznote-export --username 'your-account@example.com' --output '/path/to/Obsidian Vault/为知笔记'
```

密码会在终端中无回显输入，不作为命令行参数传递，也不会写入文件。请使用为知笔记的账号密码；错误码 `31002` 表示登录凭据被拒绝。

工具优先查找 `~/Library/Containers/*/Data/Documents/<账号>/index.db`；若没有找到账号索引，再尝试为知共享容器中的单份 widget 索引。自动定位失败或存在多份索引时，可以指定 `--db`：

```bash
wiznote-export --username 'your-account@example.com' \
  --db '/path/to/index.db' \
  --output '/path/to/export'
```

## 4. 核对结果与续跑

每篇笔记生成一个 `.md` 文件，旁边的 `<笔记名>_assets/` 目录存放图片和附件。文件名末尾附加笔记 GUID 的前八位，避免同名覆盖。`export-report.json` 记录索引数、完成数、跳过数，以及失败笔记的 GUID 和错误原因。

```bash
cat '/path/to/Obsidian Vault/为知笔记/export-report.json'
```

使用同一输出目录再次运行时，已有 Markdown 会跳过，失败笔记会重试。全部笔记处理完后才会写入报告；如仍有失败，命令以退出码 1 结束。

正文为空的笔记会列入失败报告，不会生成空 Markdown。网络超时或服务端错误也可能导致失败。工具无法自动发现本机索引以外的云端笔记，运行前请先在 App 中同步；特殊格式的失败笔记需在为知笔记中核对。

### 自己实际导出结果（2026-10-30）

实际使用本工具导出到 `~/Documents/Obsidian Vault/为知笔记`：索引 635 篇，成功导出 628 篇，失败 7 篇，生成 628 个 Markdown 文件。
