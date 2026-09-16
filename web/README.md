# 自然语言问数助手前端

Angular 21 + Material 3 + Tailwind CSS。包含智能对话页与元数据维护界面，对应仓库 `nl2sql-agent`（Knowledge-graph NL2SQL agent）。

## 启动

先确保后端 API 在 `http://127.0.0.1:8000` 运行，然后：

```bash
cd web
npm start
```

浏览器打开 [http://localhost:4200](http://localhost:4200)。开发服务器通过 `proxy.conf.json` 将 `/api` 代理到后端。

## 页面

| 路由 | 说明 |
|------|------|
| `/chat` | 智能对话（登录后默认进入） |
| `/catalog` | 数据目录（表 → 列 → 枚举，三栏布局） |
| `/knowledge` | 业务知识列表与详情 |
| `/sqls` | SQL 模板列表与内容预览 |
| `/ingest` | 导入任务历史 |

## 构建

```bash
npm run build
```

产物在 `dist/web/`。

## Development server

To start a local development server, run:

```bash
ng serve
```

Once the server is running, open your browser and navigate to `http://localhost:4200/`. The application will automatically reload whenever you modify any of the source files.

## Code scaffolding

Angular CLI includes powerful code scaffolding tools. To generate a new component, run:

```bash
ng generate component component-name
```

For a complete list of available schematics (such as `components`, `directives`, or `pipes`), run:

```bash
ng generate --help
```

## Building

To build the project run:

```bash
ng build
```

This will compile your project and store the build artifacts in the `dist/` directory. By default, the production build optimizes your application for performance and speed.

## Running unit tests

To execute unit tests with the [Vitest](https://vitest.dev/) test runner, use the following command:

```bash
ng test
```

## Running end-to-end tests

For end-to-end (e2e) testing, run:

```bash
ng e2e
```

Angular CLI does not come with an end-to-end testing framework by default. You can choose one that suits your needs.

## Additional Resources

For more information on using the Angular CLI, including detailed command references, visit the [Angular CLI Overview and Command Reference](https://angular.dev/tools/cli) page.
