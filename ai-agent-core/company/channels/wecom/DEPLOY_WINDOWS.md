# 部署手册 · 企业微信·微信客服接入(Windows 常开机)

> 给**首尔那台 Windows 上的 Claude 会话**看的。你(Claude)将在这台机器上把"企业微信·微信客服 → DeepSeek 判 → 上看板/自动回"整条链跑起来。这台是干净的韩国家庭网络(不像开发用的那台 Mac 带 Streisand/日本 VPN,DNS 有毛病)。用户在外地出差,通过网易 UU 远程操作这台机器,也可以在这台机器上帮你点企业微信后台。**全程中文跟用户交流(术语可保留原文)。**

## 0. 这是什么 / 目标
把一家张家界定制游 2 人小公司的客户消息接入做成官方、稳定、不封号、程序可读的通道。选的是**企业微信·微信客服**(个人微信被反抓截图、AX 读不到,是死路)。代码已经写好、24 个测试全过(在 Mac 上开发的),你在这台把它**真正跑起来 + 走通企业微信后台配置 + 实测收发**。

**安全红线(务必遵守):**
- 仓库是 **public**。**绝不把 Secret / AESKey / DeepSeek key 写进代码或提交、也不要打印到聊天里。** 敏感值只放本机 `~/.wecom/creds.env`(Windows 即 `%USERPROFILE%\.wecom\creds.env`)。
- **碰钱/对客户承诺 = 硬闸门**,永远上看板给人拍板,绝不自动应承(代码已内置)。
- 发任何消息给真人前先给用户确认内容。测试只在用户自己的测试群/自己账号里发。

## 1. 已经做好的(在 Mac 会话里完成,别重做)
- 企业微信企业已注册,用户=杨椿萱(管理员)。CorpID = `ww13a25bee0640c1aa`。
- 自建应用「客服人工」已建好,**AgentId = `1000002`**,有 Secret(见第 3 步,用户手上能重新查看)。
- 该应用**已授权可调用微信客服接口**(后台:微信客服 → 通过API管理会话消息 → 企业内部开发 →「可调用接口的应用」里勾了「客服人工」)。
- 已有一个客服账号(「张界市永定区映晖苑民宿中心客服…」)。
- 代码(本仓库 `company/channels/wecom/`):config / client(gettoken+sync_msg+send_msg)/ crypto(WXBizMsgCrypt 回调解密)/ callback(回调HTTP服务)/ hub(总线)/ run(入口)。

## 2. 还没做的(你在这台要完成的)
A. 建环境 + 装依赖 + 跑测试确认代码 OK。
B. 配 `~/.wecom/creds.env`(CorpID/AgentId 已知;Secret 用户提供;Callback Token/AESKey 你生成)。
C. 起回调服务 + 开穿透(cloudflared)拿到公网 HTTPS 地址。
D. 企业微信后台:给「客服人工」配**接收消息API回调 URL**(=穿透地址,Token/AESKey=creds 里那对)→ 验证通过。**这一步同时解掉"可信IP"的前置要求。**
E. 企业微信后台:配**企业可信IP** = 这台 Windows 的公网 IP。
F. 实测:换 token + 列客服账号(不再报 48002/60020)→ 让用户往客服发条测试消息 → 确认它进了看板。

## 3. 一步步做

### 3.1 前置
需要 **Python 3.10+** 和 **git**。检查:
```powershell
python --version
git --version
```
没有就让用户装(python.org / git-scm.com,或 winget install Python.Python.3.12 Git.Git)。

### 3.2 拉代码
```powershell
cd %USERPROFILE%
git clone https://github.com/yangchunxuan/ttour.git
cd ttour
git checkout feat/macos-vm-agent
cd ai-agent-core
```

### 3.3 建 venv + 装依赖
```powershell
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\pip install pycryptodome pytest
```

### 3.4 跑测试(确认代码在这台也 OK,应 24 passed)
```powershell
.venv\Scripts\python -m pytest company\channels\tests\ -q
```

### 3.5 配凭据文件 `~/.wecom/creds.env`
先建目录和已知项:
```powershell
mkdir %USERPROFILE%\.wecom 2>NUL
(
echo WECOM_CORPID=ww13a25bee0640c1aa
echo WECOM_AGENTID=1000002
) > %USERPROFILE%\.wecom\creds.env
```
**加 Secret(别打印出来):** 让用户在企业微信后台打开「客服人工」应用详情 → Secret 点「查看」→ 企业微信 App 确认 → 复制 Secret → 你引导用户执行(PowerShell 从剪贴板取,不回显):
```powershell
Add-Content %USERPROFILE%\.wecom\creds.env ("WECOM_SECRET=" + (Get-Clipboard))
```
校验长度=43、无非法字符(别打印内容):
```powershell
.venv\Scripts\python -c "import os;v=[l.split('=',1)[1].strip() for l in open(os.path.expanduser('~/.wecom/creds.env'),encoding='utf-8') if l.startswith('WECOM_SECRET=')][-1];import re;print('len',len(v),'illegal',len(re.sub(r'[A-Za-z0-9_-]','',v)))"
```
**生成 Callback Token + AESKey(你生成、写入,同时会用在企业微信后台):**
```powershell
.venv\Scripts\python -c "import os,secrets,string;p=os.path.expanduser('~/.wecom/creds.env');a=string.ascii_letters+string.digits;open(p,'a',encoding='utf-8').write('WECOM_CALLBACK_TOKEN=%s\nWECOM_CALLBACK_AESKEY=%s\n'%(''.join(secrets.choice(a) for _ in range(24)),''.join(secrets.choice(a) for _ in range(43))));print('written')"
```
把这两个值读出来(**待会儿要在企业微信后台填一份一样的;提醒用户别公开分享**):
```powershell
.venv\Scripts\python -c "import os;[print(l.strip()) for l in open(os.path.expanduser('~/.wecom/creds.env'),encoding='utf-8') if l.startswith(('WECOM_CALLBACK_TOKEN=','WECOM_CALLBACK_AESKEY='))]"
```

### 3.6 先冒烟一把(自洽验证,不联网也能过)
```powershell
.venv\Scripts\python -c "from company.channels.wecom.config import load_config;from company.channels.wecom.crypto import WXBizMsgCrypt;c=load_config();x=WXBizMsgCrypt(c.callback_token,c.callback_aeskey,c.corpid);e=x._encrypt('hi');assert x._decrypt(e)=='hi';print('crypt OK, has_callback',c.has_callback)"
```

### 3.7 起回调服务(端口 9000,后台常驻)
新开一个窗口跑(保持开着):
```powershell
.venv\Scripts\python -m company.channels.wecom.run --port 9000
```
应打印"✅ 企业微信·微信客服接入已启动"。默认不自动回(先全上看板);等验证稳了再加 `--auto-reply`。

### 3.8 开穿透(cloudflared),拿公网 HTTPS 地址
装 cloudflared:`winget install Cloudflare.cloudflared`(或从 github.com/cloudflare/cloudflared/releases 下 .exe)。
起快速隧道(**这一步是把本地服务开到公网;若被权限/沙箱拦,让用户自己在终端跑这条**):
```powershell
cloudflared tunnel --url http://localhost:9000
```
它会打印 `https://xxxx.trycloudflare.com`。**记下这个地址 = 回调 URL。**(这台是干净韩网,cloudflared 的 DNS 不会像那台 Mac 那样出问题。)
> 自测:用下面脚本模拟企业微信的 GET 验证,应回显原文(证明公网→穿透→本地→解密整条通):
```powershell
.venv\Scripts\python -c "import urllib.request,urllib.parse;from company.channels.wecom.config import load_config;from company.channels.wecom.crypto import WXBizMsgCrypt,sha1_signature;U='粘贴穿透地址';c=load_config();x=WXBizMsgCrypt(c.callback_token,c.callback_aeskey,c.corpid);p='12345';e=x._encrypt(p);s=sha1_signature(c.callback_token,'1','n',e);q=urllib.parse.urlencode({'msg_signature':s,'timestamp':'1','nonce':'n','echostr':e});r=urllib.request.urlopen(U+'/?'+q,timeout=20);print('OK' if r.read().decode()==p else 'BAD')"
```

### 3.9 企业微信后台:配「客服人工」的接收消息API回调(=解掉可信IP前置)
浏览器登 `work.weixin.qq.com`(**这个后台域名 claude-in-chrome 扩展会拦,你用 computer-use 截图看、引导用户点;Chrome 对 computer-use 是只读,所以是"用户点、你看"**)。
路径:应用管理 → 应用 → 自建 →「客服人工」→ 功能区「接收消息」→「设置API接收」→ 填:
- **URL** = 第 3.8 的穿透地址
- **Token** = creds 里的 WECOM_CALLBACK_TOKEN
- **EncodingAESKey** = creds 里的 WECOM_CALLBACK_AESKEY
- 保存 → 企业微信会向 URL 发 GET 验证,你的 run.py 会自动回显通过。**保存成功=验证通过。**

### 3.10 企业微信后台:配企业可信IP
先拿这台的公网 IP:
```powershell
.venv\Scripts\python -c "import urllib.request;print(urllib.request.urlopen('https://api.ipify.org',timeout=10).read().decode())"
```
路径:「客服人工」应用详情 → 开发者接口 →「企业可信IP」→「配置」→ 填上面的 IP → 确定。
(前置已被 3.9 满足,这次能填了。若家里宽带 IP 会变,记一笔:以后可能要重配或找宽带商要固定 IP。)

### 3.11 实测(见真章)
换 token + 列客服账号,应不再报 48002/60020:
```powershell
.venv\Scripts\python -c "from company.channels.wecom.config import load_config;from company.channels.wecom.client import from_config,WecomError;cli=from_config(load_config());\nimport sys\ntry:\n a=cli.kf_account_list();print('OK 客服账号:',[x.get('name') for x in a])\nexcept WecomError as e:print('errcode',e.errcode,e.errmsg[:60])"
```
- 通了 → 让**用户从微信扫「客服人工」客服账号的接待二维码/链接**(后台"接入场景"里可拿),给客服**发一条测试消息**。
- 看 run.py 那个窗口应有日志;再看看板库里是否落了一条 case:
```powershell
.venv\Scripts\python -c "import sqlite3,os;c=sqlite3.connect(os.path.expanduser('~/.wecom/handoff.db'));print(c.execute('select source,message,route from cases order by id desc limit 5').fetchall())"
```
出现那条客户消息 = **整条链打通** ✅。

### 3.12 看板(可选,给用户一个界面看)
```powershell
.venv\Scripts\python -m company.board --db %USERPROFILE%\.wecom\handoff.db --port 8080
```
浏览器开 `http://localhost:8080`,三栏:待你拍板 / 升级Claude / DeepSeek自理。

## 4. 常见错误码(我们踩过的)
- **48002 api forbidden** = 应用没被授权调微信客服接口,**或**该应用没配企业可信IP(企业微信会因此直接禁调)。→ 确认 3.9/3.10 都做了。
- **60020 not allow to access from your ip** = 调用方公网 IP 没进可信IP白名单。→ 把这台的公网 IP 加进 3.10。
- **40001/40013** = Secret / CorpID 不对。
- 回调保存时验证失败 = run.py 没在跑、穿透断了、或 Token/AESKey 两边不一致。→ 先用 3.8 自测脚本确认公网能回显。
- gettoken 不需要可信IP;sync_msg/send_msg/kf 列表都需要。

## 5. 接 DeepSeek(可选,先跑通管道再接)
run.py 不设 DeepSeek key 时用**保守 stub**:所有消息 escalate、全上看板、不自动回 —— 适合先验证管道。要接真判官:
```powershell
set DEEPSEEK_API_KEY=<用户的key>   （别写进代码/别提交）
set DEEPSEEK_BASE_URL=<base url>
.venv\Scripts\python -m company.channels.wecom.run --port 9000
```
（注:曾有一个旧 DeepSeek key 泄露待吊销,别用旧的,让用户给新 key。）

## 6. 7×24 常驻
验证通过后,让 run.py 和 cloudflared 常开(可用 nssm 装成 Windows 服务,或放到"启动"计划任务)。这台是常开机,断网/重启后要能自动拉起 —— 这块跟用户确认怎么弄最省心。

---
**做完把结果告诉用户:哪一步通了、哪条测试消息进了看板。遇到卡点按第 4 节排查;真卡住就把错误码和现象讲清楚,别假装成功。**
