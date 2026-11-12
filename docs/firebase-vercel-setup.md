# Firebase + Vercel 新手设置指南

目标是：网页继续部署到 Vercel，业务数据保存在 Firestore；不再连接 MySQL，不开 Firebase Storage，不迁移旧数据。

本系统的账号是**自己的邮箱／密码账号**。Firebase 在这里仅提供数据库，不负责网页登录，因此不需要启用 Firebase Authentication，也不用在浏览器代码里填写 Firebase API key 或 `firebaseConfig`。

## 1. 创建免费的 Firestore 数据库

1. 打开 [Firebase 控制台](https://console.firebase.google.com/)，选择「添加项目 / Add project」。记下 **Project ID（项目 ID）**，它通常与显示名称不同。
2. 使用 **Spark 免费方案**。当前方案不需要 Storage 或升级 Blaze；不要为了海报上传开启结算。
3. 进入「构建 / Build」→「Firestore Database」→「创建数据库 / Create database」。创建 **默认数据库 `(default)`**，使用 Firestore Native mode 的标准数据库。
4. 地区建议选 **`asia-southeast1`（Singapore，新加坡）**，适合马来西亚活动。数据库创建后无法直接更改地区，确认后再创建。
5. 安全规则选择「生产模式 / Production mode」。在「规则 / Rules」页，将仓库的 [firestore.rules](../firestore.rules) 全部复制进去并发布。

规则内容的核心是：

```text
allow read, write: if false;
```

这是正确设置。浏览器不能直接读写数据库；Flask 在服务器检查登录、权限和 CSRF 后，使用服务账号访问 Firestore。服务器 SDK 通过 Google Cloud IAM 授权，不依赖上述客户端规则。**不要改成 `if true`。**

如使用 Firebase CLI 部署规则，可在仓库根目录执行：

```bash
npx --yes firebase-tools@15.36.0 login
npx --yes firebase-tools@15.36.0 deploy --only "firestore:rules,firestore:indexes" --project YOUR_PROJECT_ID
```

将 `YOUR_PROJECT_ID` 替换为自己的项目 ID。项目已有其他应用时，先确认这些客户端全拒绝规则适用于同一个数据库。本仓库面向独立 I-RICE 项目。

`firestore.indexes.json` 暂无额外复合索引；默认单字段索引仍保留。当前列表查询／排序由服务器按小型活动规模处理。

## 2. 创建服务器专用的服务账号

建议为此应用单独创建服务账号，而不是给它项目 Owner 权限：

1. 在 Firebase 项目设置的「服务账号 / Service accounts」中，进入管理服务账号的 Google Cloud 页面；也可打开 [Google Cloud 服务账号](https://console.cloud.google.com/iam-admin/serviceaccounts)，确认选中刚才的 Firebase 项目。
2. 选择「创建服务账号 / Create service account」，名称例如 `irice-server`。
3. 给它分配 **Cloud Datastore User**，角色 ID 是 **`roles/datastore.user`**。该角色允许服务器读写 Firestore 业务数据，不需要 Owner／Editor。
4. 创建后打开这个服务账号，进入「密钥 / Keys」→「添加密钥 / Add key」→「创建新密钥 / Create new key」→「JSON」。
5. 下载 JSON 文件，放在本机仓库之外的私密目录。这个文件包含私钥，不能提交到 GitHub、不能公开分享，也不要发到聊天中。

如果组织策略禁止创建 JSON 密钥，需要项目管理员提供受支持的服务器认证方式；不要改规则开放数据库来绕过认证。

## 3. 设置 Vercel 环境变量

在 Vercel 导入 `ChinoGan520/FYP`，或打开已有项目的 **Settings → Environment Variables**。确认部署分支已经包含本次 Firestore 版本，项目根目录能看到 `app.py` 与 `requirements.txt`。

设置以下变量。至少应用到 **Production**；要使用 Preview 部署，也应对 Preview 安全配置对应值。

| 变量 | 填什么 |
| --- | --- |
| `FIREBASE_PROJECT_ID` | 第 1 步的实际项目 ID，不是项目显示名称。 |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | 第 2 步下载的 **完整 JSON 原文**。在安全变量输入框中粘贴整个文件内容，不是文件名／路径，也不是只粘贴 `private_key`。 |
| `SECRET_KEY` | 至少 32 字符的强随机值，建议用下面命令生成的 64 位十六进制字符串。固定保存，不要每次部署更换。 |
| `COOKIE_SECURE` | `1`，Vercel 使用 HTTPS。 |
| `DATA_BACKEND` | 可省略（默认 Firestore），或明确填写 `firestore`。 |

在自己的电脑生成随机值：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

只将自己生成的结果保存在安全设置／私密本机配置中。不要把它放到 GitHub 或使用本说明里的占位文字。

同时移除这个 Vercel 项目中已不使用的 `DATABASE_URL`、MySQL／Aiven 连接配置，以及本机专用的 `GOOGLE_APPLICATION_CREDENTIALS`、`FIRESTORE_EMULATOR_HOST`、`SQLITE_PATH`。云端认证使用完整 JSON 变量，本机文件路径在 Vercel 上不存在。

保存变量后 **重新部署 / Redeploy**；已经运行的部署不会自动读取新设置。保留 Vercel 对根目录 Flask 应用的默认构建识别，不需要 Docker、MySQL、`app.run` 或常驻数据库启动命令。

建议把 Firebase JSON 变量标记为敏感信息。浏览器代码、网页、错误页面和日志都不应该显示它。

## 在自己的电脑初始化数据库与管理员

初始化只需要执行一次，之后可安全重复补充缺少的默认数据。网页部署不会在每次冷启动时自动创建类别和展位，以免产生不必要的写入。

先安装 Python 3.12+，把最新仓库下载／拉取到自己的电脑。下面是在**仓库根目录**执行的步骤。

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
New-Item -ItemType Directory -Force instance | Out-Null
Copy-Item .env.example instance\.env
```

使用编辑器打开 `instance\.env`：

- `FIREBASE_PROJECT_ID` 填与 Vercel 一样的项目 ID。
- `GOOGLE_APPLICATION_CREDENTIALS` 填本机私密 JSON 文件的**绝对路径**；Windows 可使用正斜杠，例如 `C:/Users/yourname/private/irice-service-account.json`。
- `SECRET_KEY` 填强随机值，不保留 `REPLACE_WITH...`。本机可用自己单独的随机值；Vercel 上的值保持稳定。
- `COOKIE_SECURE=0` 用于本机 HTTP。Vercel 中仍设为 `1`。
- 保持 `DATA_BACKEND=firestore`；不要开启示例中的模拟器或 SQLite 选项。

然后执行：

```powershell
.\.venv\Scripts\python.exe -m flask --app irice init-db
.\.venv\Scripts\python.exe -m flask --app irice create-admin --email you@example.com
```

将示例邮箱替换成自己想用的组织者邮箱。第二条命令会在终端询问姓名、密码和确认密码；密码不回显。创建后可直接在 Vercel 网页使用这个账号登录，因为两边连接的是同一个 Firestore 项目。

本机运行网页：

```powershell
.\.venv\Scripts\python.exe -m flask --app irice run --port 5000
```

这些命令不要求激活虚拟环境，也无需更改 PowerShell 执行策略。

### macOS / Linux

```bash
bash scripts/setup.sh
mkdir -p instance
cp .env.example instance/.env
# 用编辑器填写 instance/.env，规则与 Windows 说明相同。
.venv/bin/python -m flask --app irice init-db
.venv/bin/python -m flask --app irice create-admin --email you@example.com
.venv/bin/python -m flask --app irice run --port 5000
```

`init-db` 创建默认类别和 20 个默认展位，不会清空已有数据。组织者账号没有默认密码，普通注册表单也不能创建管理员。如果重复创建已有邮箱，命令会拒绝覆盖，避免改变现有账号的角色或密码。

本机 `instance/.env` 在 Git 忽略列表中。配置模板 `.env.example` 只能保留占位文字。服务账号 JSON 仍应放在仓库之外。

## 5. 检查你的真实部署

以下操作需要你自己的 Firebase 配置生效；本机测试／模拟器通过不能替代它们。

1. 打开 Vercel 实际生成的部署网址，并检查该部署下的 `/health` 返回成功且数据库连接正常。未配置凭据或连接失败应返回不可用状态，而非假成功。
2. 用刚才创建的组织者邮箱／密码登录，确认 **Booth management** 中有默认类别及展位。
3. 在另外的浏览器会话注册参展者，完善团队并提交项目摘要。海报区域应明确显示已停用，不能上传文件。
4. 组织者批准项目并分配兼容展位；参展者在 **My digital pass** 查看 QR 和下载 PDF。
5. 用组织者的 **Event check-in** 扫描／手动验证 QR。第一次记录签到，重复扫描显示原记录，不增加人数。
6. 查看 Firestore 控制台和后台统计；确认数据在重新部署后仍保留。

如果真实项目尚未配置服务账号，当前只能说明本机／模拟器验证结果，不能宣称真实 Firebase 或 Vercel 已验证成功。

## 6. 本机模拟器与免费额度

Firestore Emulator 用于无真实凭据的开发和自动测试，不消耗云项目配额。需要受 Firebase CLI 支持的 Node.js 版本及 **Java 21**。仓库测试脚本固定使用 `demo-irice-test` 演示项目和本机端口，不应改成正式项目。

```bash
bash scripts/setup.sh
bash scripts/test-firestore.sh
```

脚本使用固定版本 `firebase-tools@15.36.0`；若本机已安装该 CLI，可通过 `FIREBASE_CLI` 指定可执行文件。首次运行需要正常下载官方模拟器文件。若网络／组织策略阻止下载，应修复访问权限，不关闭 TLS、签名或校验检查。

Windows 可在 Git Bash / WSL 中运行该脚本。只运行隔离业务测试也可以：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

本机替代后端必须显式设置 `DATA_BACKEND=sqlite`；它使用 Python 标准库的本地文档存储，仅用于本机开发和测试，不是云端数据库，也不能作为 Vercel 持久化方案。

Firebase Spark 有免费配额，并非无限容量或无限请求。标准 Firestore 免费配额通常包括 1 GiB 存储和每天 50,000 次读取、20,000 次写入、20,000 次删除；准确限制和可用性以 [Firestore 配额说明](https://firebase.google.com/docs/firestore/quotas) 与 [Firebase 价格页](https://firebase.google.com/pricing) 为准。Vercel 免费方案也有自己的资源与使用限制。

本应用每次打开或手动刷新列表／统计都需要读取记录。仪表盘不自动轮询；需要最新数据时点击 Refresh。在线通行证仍自动刷新短期 QR，以保持入场验证有效。演示和重复自动测试请用模拟器，避免不必要地反复刷新真实项目。当前没有 Storage、支付或付费海报功能；未来若增加这些能力，应另外评估费用。

## 常见问题

- **`/health` 不可用：**先检查 Vercel 当前部署的项目 ID、完整 JSON 变量和新部署是否生效；确认服务账号对该项目有 `Cloud Datastore User` 权限。不要把完整密钥粘贴到日志或聊天中排错。
- **有页面，但没有类别／展位：**在本机对同一个 Firebase 项目运行 `init-db`。不要寻找公开的“初始化管理员”网址；系统没有该入口。
- **本机登录后又回到登录页：**HTTP 本机开发使用 `COOKIE_SECURE=0`。HTTPS 的 Vercel 部署保留 `1`。
- **普通注册看不到管理后台：**这是预期行为。只可通过受信任的 `create-admin` 命令创建组织者。
- **Firestore 规则拒绝客户端访问：**这是预期行为。系统通过服务器访问，不能为此把规则改为公开。
- **相机打不开：**确认部署使用 HTTPS、浏览器已允许相机，并在实际设备上测试；可使用相同验证逻辑的手动 token 字段。
- **旧 MySQL 数据没有出现：**这次按用户确认的全新 Firestore 开始，没有迁移旧数据。旧 Aiven 服务与旧本机数据不会被此应用自动删除。

## `/health` 的安全诊断代号

连接失败时 HTTP503 的 JSON 会包含 `reason`。这里只显示固定代号，不回传密钥、原始异常、完整服务账号或连接信息；可以只分享这个代号排错。

| reason | 下一步 |
| --- | --- |
| `PROJECT_ID_MISSING` | 在当前部署环境设置 `FIREBASE_PROJECT_ID`，重新部署。 |
| `SERVICE_ACCOUNT_MISSING` | 在 Vercel 设置完整 `FIREBASE_SERVICE_ACCOUNT_JSON`，重新部署。 |
| `SERVICE_ACCOUNT_JSON_INVALID` | 确认粘贴完整服务账号 JSON 对象，而不是文件路径、网页 firebaseConfig 或包在额外引号内的字符串。 |
| `PROJECT_ID_MISMATCH` | 环境变量项目 ID 必须和 JSON 内的 `project_id` 完全一致。 |
| `SERVICE_ACCOUNT_INVALID` | 重新下载有效服务账号 JSON，并完整保留私钥的 JSON 转义格式。不要公开私钥。 |
| `FIRESTORE_PERMISSION_DENIED` | 确认服务账号对目标项目有 `Cloud Datastore User` 权限，且 Cloud Firestore API 已启用；客户端规则无需开放。 |
| `FIRESTORE_NOT_FOUND` | 确认目标项目已建立 Firestore Native／Standard 的 `(default)` 数据库。 |
| `FIRESTORE_UNAUTHENTICATED` | 检查服务账号／密钥是否已停用、删除或撤销；在安全设置中更新有效凭据后重新部署。 |
| `FIRESTORE_NETWORK_UNAVAILABLE` | 检查平台到 Google 服务的连接与暂时性故障；这不等于密码错误。 |
| `EMULATOR_NOT_ALLOWED` | 从 Vercel 移除 `FIRESTORE_EMULATOR_HOST`，正式部署只能连接真实项目。 |
| `FIRESTORE_CONFIG_INVALID` 或 `BACKEND_UNAVAILABLE` | 已知代号不足以确定原因，继续检查部署设置及受保护的运行环境；不要把完整凭据写入日志。 |
