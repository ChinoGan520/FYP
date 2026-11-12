# I-RICE 展览管理系统

用于 Southern University College I-RICE 活动的响应式网页系统：参展项目报名、审核、展位分配、个人电子通行证和现场 QR 签到。

当前技术栈是 **Python Flask + Firebase Cloud Firestore + Bootstrap 5**。前端仍使用 Flask 页面和本地静态资源，登录仍使用本系统自己的邮箱、密码与会话；不需要 Firebase Authentication、前端 Firebase 配置或 Firebase Storage。

## 当前范围

- **参展者：**注册、维护团队、提交标题／类别／摘要／展位要求、查看审核意见和展位、下载个人入场证与展位证书。
- **观众／评审：**免费预约入场，由组织者审核后领取个人通行证。评审账号没有后台管理权限。
- **组织者：**审核与重新开放申请、分配兼容类别的展位、管理类别及展位、审核观众、查看统计与活动记录、导出 CSV、相机或手动 QR 签到。
- **验证：**现场 QR 有效期 180 秒，在线页面自动刷新；下载的 PDF QR 有效期 24 小时。每次扫描都检查最新批准状态，每个注册账号只记录一次签到。

本次按全新 Firestore 数据库开始，**不会读取或迁移旧 Aiven／MySQL 数据**。为维持无需 Storage／结算账号的方案，**海报上传与查看已停用**。过去的 MySQL、SQLAlchemy、Docker 数据库脚本和连接变量不再使用。

## 第一次部署到 Vercel

按 [Firebase + Vercel 新手设置指南](docs/firebase-vercel-setup.md) 操作，顺序是：

1. 创建 Firebase **Spark 免费方案**项目，在默认数据库中启用 Firestore，位置建议选择新加坡。
2. 创建只供 Flask 服务器使用的服务账号，授予 `Cloud Datastore User` 权限，安全保存 JSON 密钥。
3. 在 Vercel 设置 `FIREBASE_PROJECT_ID`、`FIREBASE_SERVICE_ACCOUNT_JSON`、随机且固定的 `SECRET_KEY`、`COOKIE_SECURE=1`，然后部署。
4. 在自己的电脑上执行 `init-db` 初始化默认类别／20 个展位，再执行 `create-admin` 创建组织者。
5. 打开部署页面，验证报名 → 审核 → 分配展位 → 通行证 → 签到完整流程。

**服务账号 JSON 和密钥只放安全的环境变量或本机私密文件，不要提交到 GitHub，也不要发到聊天中。** Firestore 浏览器规则保持拒绝全部客户端读写；服务器通过服务账号 IAM 访问。

## 本机开发

需要 Python 3.12+。不需要 Docker 或常驻 MySQL 服务。使用现有仓库目录即可。

macOS／Linux：

```bash
bash scripts/setup.sh
mkdir -p instance
cp .env.example instance/.env
# 编辑 instance/.env：填写项目 ID、私密 JSON 文件的绝对路径和随机 SECRET_KEY。
.venv/bin/python -m flask --app irice init-db
.venv/bin/python -m flask --app irice create-admin
.venv/bin/python -m flask --app irice run --port 5000
```

Windows PowerShell 的对应命令和变量设置见 [新手设置指南](docs/firebase-vercel-setup.md#在自己的电脑初始化数据库与管理员)。

`init-db` 可重复运行：只补充缺少的默认类别与展位，不清空报名数据。`create-admin` 在终端安全提示输入姓名、邮箱和密码，没有默认管理员密码，也没有公开创建管理员的 HTTP 接口。普通网页注册无法获得组织者权限。

本机还可使用 Firestore Emulator 做开发／测试；它不消耗真实 Firestore 配额。仅用于本机的显式 `DATA_BACKEND=sqlite` 文档存储替身也可用，不能用于 Vercel 持久化。详见指南与测试脚本。

## 验证和测试

```bash
.venv/bin/python -m pytest -q
bash scripts/test-firestore.sh
```

第一条使用隔离的临时 SQLite 文档存储验证业务逻辑；第二条使用真实 Firestore Emulator 验证同一流程及事务行为，需要 Node.js、Java 21 和可用的模拟器下载网络。模拟器目标固定为演示项目，不测试或清空真实云项目。

测试涵盖注册、角色与所有权、CSRF、海报停用、项目审核／重提、展位兼容性与并发分配、QR 篡改／过期／撤销／重复签到、PDF、CSV 和页面渲染。模拟器通过不等于你的真实 Firebase 项目已连接；填写真实凭据后仍需运行部署检查。

可选的虚构示例数据：

```bash
.venv/bin/python -m flask --app irice seed-demo
```

示例团队标记为 `[DEMO]`，密码随机且不公开。此命令不自动运行；若只想保持正式数据库干净，可在模拟器中演示。

## 使用与免费额度

- Firebase 免费方案有容量和每日读写配额，并非无限免费。此系统面向小型单活动；列表、统计和手动刷新会产生读取；仪表盘不自动轮询。测试优先使用模拟器，使用量以 Firebase 控制台和官方价格为准。
- 海报文件、Firebase Storage、支付、原生手机 App、离线验票、密码找回邮件和评审打分不在当前范围。免费预约加组织者批准就是入场预约，不收款。
- 相机需 HTTPS 和设备权限；应在活动实际手机／浏览器上检查。手动 token 签到使用相同的服务器验证。
- 团队名单不自动创建账号。每个实际入场的人需要独立注册和批准；签到统计按人，项目和展位统计按项目。
- 保持 `SECRET_KEY` 稳定；更换它会使现有会话和 QR 失效。部署时必须提供强随机值，无硬编码后备密钥。
- Vercel 临时文件系统不能保存业务数据库或上传文件。正式数据放 Firestore；服务进程与本机文件不承担云端持久化。

更多设计说明：[架构和业务流程](docs/architecture.md)。
