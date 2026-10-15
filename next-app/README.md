# Next.js example application

茶铺示例使用 Next.js 16.3.2 和 React 19.2.8，依赖版本由本目录的 `package-lock.json` 固定。它只使用客户端界面状态，不包含商品数据库、账号系统、支付或正式发布。

工作台首次连接新沙箱时会使用这里的源码。也可以使用 Node.js 22，在本目录运行 `npm ci`、`npm run dev`，单独查看应用；这不会启动 Agent 工作台。

`app/api/health/route.js` 提供预览健康检查。页面保留折叠的草稿与计数器，用于观察 Fast Refresh 是否保留浏览器状态。
