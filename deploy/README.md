# 部署指南（让服务 7×24 在线，不再依赖沙箱）

> 评审要求：① 链接匿名可打开 ② 不依赖本机环境 ③ 数据合规 ④ 不下线、不换链接。
> 本应用是标准 FastAPI 单端口服务，镜像内自带全部数据（209 家餐厅 + 167 道菜谱 + 营养表），
> **不依赖任何数据库服务器、不读本机文件、不需要登录**，满足 ②③。

---

## 方案 A：Render 免费部署（推荐，0 元，约 10 分钟）

### 步骤

1. 注册 [render.com](https://render.com)（用 GitHub 账号登录最快）
2. 把本项目推到你的 GitHub 仓库（新建 repo → 上传本目录，`config.json` 里的 Key 留空）
3. Render 控制台 → **New → Blueprint** → 选该仓库（会自动读取 `render.yaml`）
4. 在环境变量里填 `LLM_KEY`（智谱 Key），保存
5. 等待首次构建（约 3~5 分钟），得到形如 `https://food-butler-xxxx.onrender.com` 的地址

### 注意

- **免费实例闲置 15 分钟会休眠**，下次访问冷启动约 30~60 秒。若希望评审期间始终秒开，把 `plan: free` 改成 `starter`（约 $7/月）
- 免费实例的磁盘**不保证持久**：`data/app.db`（用户画像/饮食记录）可能丢失。餐厅与菜谱数据在镜像里，不受影响
- 链接是**公开的，无需 sharecode**，直接用无痕窗口就能打开

### 更新方式（遵守"别换链接"）

改代码 → `git push` → Render 自动重新部署，**域名不变**。

---

## 方案 B：国内轻量云服务器（约 99 元/年，最稳）

适合要求"评审期间绝对不冷启动"的场景。

1. 买一台 Ubuntu 22.04 轻量服务器（2 核 2G 足够）
2. 安全组放行 **8000** 端口
3. 上传代码后执行：

```bash
sudo mkdir -p /opt/food-butler && sudo chown $USER /opt/food-butler
# 将项目文件放到 /opt/food-butler
cd /opt/food-butler
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp deploy/food-butler.env.example /etc/food-butler.env && sudo chmod 600 /etc/food-butler.env
# 编辑 /etc/food-butler.env 填入 LLM_KEY
sudo cp deploy/food-butler.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now food-butler
curl -s 127.0.0.1:8000/api/health
```

访问：`http://服务器IP:8000`（用 IP 访问**不需要备案**；绑域名走 80/443 才需要 ICP 备案）

### 更新方式

```bash
sudo systemctl restart food-butler   # 重启即生效，链接/端口不变
```

---

## 方案 C：临时演示（当前沙箱链接）

- 优点：0 成本、立刻可用
- 缺点：**实例闲置会被回收**，返回 502，需要有人重新发布
- 只适合自己演示，**不建议作为评审链接**

---

## 上线前自查清单

| 检查项 | 命令 / 操作 | 期望 |
|---|---|---|
| 匿名可开 | 无痕窗口打开链接（不带任何登录态） | 正常显示页面 |
| 数据齐全 | `GET /api/health` | `restaurants: 209` |
| 菜谱可用 | `GET /api/recipes?limit=3` | 返回 3 道 |
| 天气推荐 | `GET /api/recommend` | 有 `env` 与 3 张卡片 |
| AI 可用 | 发一句「两个人半小时减脂」 | `mode: cook` |
| 无本机依赖 | 镜像内 `ls data/` | 有 json，无绝对路径引用 |
