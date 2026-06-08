import path from "node:path";
import process from "node:process";
import { createRequire } from "node:module";

const clientRoot = process.env.CODE_GRAPH_CLIENT_ROOT || path.resolve(process.cwd(), "client");
const requireFromClient = createRequire(path.join(clientRoot, "package.json"));
const babelParser = requireFromClient("@babel/parser");
const { parse: parseVueSfc } = requireFromClient("@vue/compiler-sfc");

const HTTP_METHOD_BY_PROPERTY = {
  get: "GET",
  post: "POST",
  put: "PUT",
  patch: "PATCH",
  delete: "DELETE"
};
const LOCAL_ABSOLUTE_URL_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);

const input = JSON.parse(await readStdin());
const extractorConfig = normalizeExtractorConfig(input.config);

try {
  const extracted = input.language === "vue"
    ? extractVueStructure(input.source, input.path)
    : extractJavaScriptStructure(input.source, 0, input.path);
  process.stdout.write(JSON.stringify({ ...extracted, unsupported: [] }));
} catch (error) {
  process.stdout.write(JSON.stringify({
    imports: [],
    routes: [],
    apiCalls: [],
    pageNavigations: [],
    unsupported: [
      {
        reason: input.language === "vue" ? "vue_parse_error" : "js_parse_error",
        message: `${error.name || "ParseError"}: ${error.message}`,
        line: Number.isInteger(error.loc?.line) ? error.loc.line : null
      }
    ]
  }));
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}

function extractVueStructure(source, filename) {
  const parsed = parseVueSfc(source, { filename });
  if (parsed.errors.length > 0) {
    throw parsed.errors[0];
  }

  const structure = { imports: [], routes: [], apiCalls: [], pageNavigations: [] };
  for (const block of [parsed.descriptor.script, parsed.descriptor.scriptSetup]) {
    if (!block) {
      continue;
    }
    const lineOffset = Math.max((block.loc?.start?.line || 1) - 1, 0);
    const blockStructure = extractJavaScriptStructure(block.content, lineOffset, filename);
    structure.imports.push(...blockStructure.imports);
    structure.apiCalls.push(...blockStructure.apiCalls);
    structure.pageNavigations.push(...blockStructure.pageNavigations);
  }
  return structure;
}

function extractJavaScriptImports(source, lineOffset) {
  return extractJavaScriptStructure(source, lineOffset, "").imports;
}

function extractJavaScriptStructure(source, lineOffset, filename) {
  const ast = babelParser.parse(source, {
    sourceType: "module",
    plugins: ["dynamicImport", "importMeta"]
  });
  const imports = [];
  walk(ast.program, (node) => {
    if ((node.type === "ImportDeclaration" || node.type === "ExportNamedDeclaration" || node.type === "ExportAllDeclaration") && node.source?.value) {
      imports.push(importRecord(node.source.value, node.loc?.start?.line, lineOffset));
      return;
    }
    if (node.type === "CallExpression" && node.callee?.type === "Import" && node.arguments?.[0]?.type === "StringLiteral") {
      imports.push(importRecord(node.arguments[0].value, node.loc?.start?.line, lineOffset));
    }
  });
  const useApiBindings = extractorConfig.frontendApiCallsEnabled
    ? collectUseApiBindings(ast.program)
    : { requestIdentifiers: new Set(), clientIdentifiers: new Set() };
  return {
    imports,
    routes: extractorConfig.isVueRouterFile ? extractVueRouterRoutes(ast.program, lineOffset) : [],
    apiCalls: extractorConfig.frontendApiCallsEnabled
      ? extractApiCalls(ast.program, lineOffset, useApiBindings)
      : [],
    pageNavigations: extractorConfig.playwrightEnabled ? extractPageNavigations(ast.program, lineOffset) : []
  };
}

function importRecord(specifier, line, lineOffset) {
  return {
    specifier,
    line: Number.isInteger(line) ? line + lineOffset : null
  };
}

function extractVueRouterRoutes(program, lineOffset) {
  const routeArrays = [];
  const seen = new Set();
  walk(program, (node) => {
    if (node.type === "VariableDeclarator" && node.id?.type === "Identifier" && node.id.name === "routes" && node.init?.type === "ArrayExpression") {
      if (!seen.has(node.init)) {
        seen.add(node.init);
        routeArrays.push(node.init);
      }
      return;
    }
    if (node.type === "CallExpression" && node.callee?.type === "Identifier" && node.callee.name === "createRouter") {
      const config = node.arguments?.[0];
      if (config?.type !== "ObjectExpression") {
        return;
      }
      const routesProperty = findObjectProperty(config, "routes");
      if (routesProperty?.value?.type === "ArrayExpression" && !seen.has(routesProperty.value)) {
        seen.add(routesProperty.value);
        routeArrays.push(routesProperty.value);
      }
    }
  });
  return routeArrays.flatMap((routeArray) => extractRouteArray(routeArray, "", lineOffset));
}

function collectUseApiBindings(program) {
  const requestIdentifiers = new Set();
  const clientIdentifiers = new Set();
  walk(program, (node) => {
    if (node.type !== "VariableDeclarator" || !isUseApiCall(node.init)) {
      return;
    }
    if (node.id?.type === "Identifier") {
      clientIdentifiers.add(node.id.name);
      return;
    }
    if (node.id?.type !== "ObjectPattern") {
      return;
    }
    for (const property of node.id.properties || []) {
      if (property.type !== "ObjectProperty" || propertyKeyName(property.key) !== "request") {
        continue;
      }
      if (property.value?.type === "Identifier") {
        requestIdentifiers.add(property.value.name);
      }
    }
  });
  return { requestIdentifiers, clientIdentifiers };
}

function isUseApiCall(node) {
  return (
    node?.type === "CallExpression" &&
    node.callee?.type === "Identifier" &&
    extractorConfig.useApiFactoryNames.has(node.callee.name)
  );
}

function extractApiCalls(program, lineOffset, useApiBindings) {
  const calls = [];
  walk(program, (node) => {
    if (node.type !== "CallExpression") {
      return;
    }
    const apiCall = apiCallFromExpression(node, lineOffset, useApiBindings);
    if (apiCall) {
      calls.push(apiCall);
    }
  });
  return calls;
}

function apiCallFromExpression(node, lineOffset, useApiBindings) {
  const pathArgument = node.arguments?.[0];
  const rawPath = apiPathArgumentValue(pathArgument);
  if (!rawPath) {
    return null;
  }

  if (node.callee?.type === "Identifier" && node.callee.name === "fetch") {
    const normalizedPath = normalizeApiPath(rawPath, false);
    if (!normalizedPath) {
      return null;
    }
    return apiCallRecord(normalizedPath, methodFromOptions(node.arguments?.[1]), "fetch", node, lineOffset);
  }

  if (
    node.callee?.type === "MemberExpression" &&
    identifierName(node.callee.object) === "window" &&
    propertyName(node.callee.property) === "fetch"
  ) {
    const normalizedPath = normalizeApiPath(rawPath, false);
    if (!normalizedPath) {
      return null;
    }
    return apiCallRecord(normalizedPath, methodFromOptions(node.arguments?.[1]), "window.fetch", node, lineOffset);
  }

  if (node.callee?.type === "Identifier" && useApiBindings.requestIdentifiers.has(node.callee.name)) {
    return apiCallRecord(
      normalizeApiPath(rawPath, true),
      methodFromOptions(node.arguments?.[1]),
      "use_api_request",
      node,
      lineOffset,
      "high"
    );
  }

  if (node.callee?.type === "Identifier" && node.callee.name === "request") {
    return apiCallRecord(
      normalizeApiPath(rawPath, true),
      methodFromOptions(node.arguments?.[1]),
      "request",
      node,
      lineOffset,
      "medium"
    );
  }

  if (node.callee?.type !== "MemberExpression") {
    return null;
  }
  const property = propertyName(node.callee.property);
  const receiverName = identifierName(node.callee.object);
  const isUseApiReceiver = useApiBindings.clientIdentifiers.has(receiverName);
  const isFallbackApiReceiver = extractorConfig.apiClientNames.has(receiverName);
  if (!isUseApiReceiver && !isFallbackApiReceiver) {
    return null;
  }
  if (property === "request") {
    return apiCallRecord(
      normalizeApiPath(rawPath, true),
      methodFromOptions(node.arguments?.[1]),
      isUseApiReceiver ? "use_api_request" : "member.request",
      node,
      lineOffset,
      isUseApiReceiver ? "high" : "medium"
    );
  }
  const method = HTTP_METHOD_BY_PROPERTY[property];
  if (!method) {
    return null;
  }
  return apiCallRecord(
    normalizeApiPath(rawPath, true),
    method,
    `member.${property}`,
    node,
    lineOffset,
    isUseApiReceiver ? "high" : "medium"
  );
}

function apiPathArgumentValue(node) {
  const directValue = stringValue(node);
  if (directValue) {
    return directValue;
  }
  const templateValue = templatePathValue(node);
  if (templateValue) {
    return templateValue;
  }
  if (node?.type !== "CallExpression" || node.callee?.type !== "Identifier") {
    return null;
  }
  if (!extractorConfig.apiUrlHelperNames.has(node.callee.name)) {
    return null;
  }
  const helperPath = stringValue(node.arguments?.[0]) || templatePathValue(node.arguments?.[0]);
  if (!helperPath) {
    return null;
  }
  const normalizedHelperPath = normalizeRoutePath(helperPath);
  return isApiBasePath(normalizedHelperPath)
    ? normalizedHelperPath
    : `${extractorConfig.apiBase}${normalizedHelperPath}`;
}

function templatePathValue(node) {
  if (node?.type !== "TemplateLiteral" || node.expressions.length === 0) {
    return null;
  }
  const firstText = templateElementValue(node.quasis[0]);
  if (!firstText || firstText === "/") {
    return null;
  }
  let value = "";
  for (let index = 0; index < node.quasis.length; index += 1) {
    value += templateElementValue(node.quasis[index]);
    if (index < node.expressions.length) {
      value += "{param}";
    }
  }
  return value;
}

function apiCallRecord(pathValue, method, callKind, node, lineOffset, confidence = "high") {
  if (!pathValue) {
    return null;
  }
  const recordConfidence = pathValue.includes("{param}") && confidence === "high" ? "medium" : confidence;
  return {
    method,
    path: pathValue,
    callKind,
    confidence: recordConfidence,
    line: lineWithOffset(node.loc?.start?.line, lineOffset)
  };
}

function methodFromOptions(node) {
  if (!node) {
    return "GET";
  }
  if (node.type !== "ObjectExpression") {
    return null;
  }
  const methodProperty = findObjectProperty(node, "method");
  if (!methodProperty) {
    return "GET";
  }
  const methodValue = stringValue(methodProperty.value);
  return methodValue ? methodValue.toUpperCase() : null;
}

function normalizeApiPath(pathValue, addApiPrefix) {
  let pathname = pathWithoutSearchOrHash(pathValue);
  if (!pathname) {
    return null;
  }
  if (/^https?:\/\//i.test(pathname)) {
    try {
      const url = new URL(pathname);
      if (!LOCAL_ABSOLUTE_URL_HOSTS.has(url.hostname)) {
        return null;
      }
      pathname = url.pathname;
    } catch {
      return null;
    }
  }
  pathname = pathname.replace(/\/+/g, "/");
  if (!pathname.startsWith("/")) {
    pathname = `/${pathname}`;
  }
  if (pathname.length > 1 && pathname.endsWith("/")) {
    pathname = pathname.slice(0, -1);
  }
  if (addApiPrefix && extractorConfig.apiBase !== "/" && !isApiBasePath(pathname)) {
    pathname = `${extractorConfig.apiBase}${pathname}`;
  }
  if (!addApiPrefix && !isApiBasePath(pathname)) {
    return null;
  }
  return pathname;
}

function isApiBasePath(pathname) {
  if (extractorConfig.apiBase === "/") {
    return pathname.startsWith("/");
  }
  return pathname === extractorConfig.apiBase || pathname.startsWith(`${extractorConfig.apiBase}/`);
}

function extractPageNavigations(program, lineOffset) {
  const navigations = [];
  walk(program, (node) => {
    if (node.type !== "CallExpression") {
      return;
    }
    const routePath = pageNavigationPath(node);
    if (!routePath) {
      return;
    }
    navigations.push({
      routePath,
      kind: pageNavigationKind(node),
      line: lineWithOffset(node.loc?.start?.line, lineOffset)
    });
  });
  return navigations;
}

function pageNavigationPath(node) {
  if (node.callee?.type !== "MemberExpression") {
    return null;
  }
  if (identifierName(node.callee.object) === "page" && propertyName(node.callee.property) === "goto") {
    return normalizeNavigationPath(stringValue(node.arguments?.[0]));
  }
  if (propertyName(node.callee.property) !== "toHaveURL" || !isExpectPageCall(node.callee.object)) {
    return null;
  }
  const value = stringValue(node.arguments?.[0]) || regexNavigationPathValue(node.arguments?.[0]);
  return normalizeNavigationPath(value);
}

function pageNavigationKind(node) {
  if (node.callee?.type === "MemberExpression" && propertyName(node.callee.property) === "toHaveURL") {
    return "playwright_to_have_url";
  }
  return "playwright_page_goto";
}

function isExpectPageCall(node) {
  return (
    node?.type === "CallExpression" &&
    node.callee?.type === "Identifier" &&
    node.callee.name === "expect" &&
    identifierName(node.arguments?.[0]) === "page"
  );
}

function regexNavigationPathValue(node) {
  const pattern = node?.type === "RegExpLiteral" ? node.pattern : node?.regex?.pattern;
  if (typeof pattern !== "string" || !pattern) {
    return null;
  }
  let literal = pattern.replace(/^\^/, "").replace(/\$$/, "");
  literal = literal.replace(/\\\//g, "/").replace(/\\\?/g, "?").replace(/\\#/g, "#");
  if (/^https?:\/\//i.test(literal)) {
    return literal;
  }
  if (literal.startsWith("//")) {
    return null;
  }
  const pathStart = literal.indexOf("/");
  if (pathStart < 0) {
    return null;
  }
  literal = literal.slice(pathStart);
  if (/[()[\]{}|+*]/.test(literal) || literal.includes("\\")) {
    return null;
  }
  return literal;
}

function normalizeNavigationPath(pathValue) {
  if (!pathValue) {
    return null;
  }
  let pathname = pathWithoutSearchOrHash(pathValue);
  if (/^https?:\/\//i.test(pathname)) {
    try {
      const url = new URL(pathname);
      if (!LOCAL_ABSOLUTE_URL_HOSTS.has(url.hostname)) {
        return null;
      }
      pathname = url.pathname;
    } catch {
      return null;
    }
  }
  return normalizeRoutePath(pathname);
}

function pathWithoutSearchOrHash(pathValue) {
  const hashIndex = pathValue.indexOf("#");
  const withoutHash = hashIndex >= 0 ? pathValue.slice(0, hashIndex) : pathValue;
  const queryIndex = withoutHash.indexOf("?");
  return queryIndex >= 0 ? withoutHash.slice(0, queryIndex) : withoutHash;
}

function extractRouteArray(routeArray, parentPath, lineOffset) {
  const routes = [];
  for (const element of routeArray.elements || []) {
    if (element?.type !== "ObjectExpression") {
      continue;
    }
    routes.push(...extractRouteObject(element, parentPath, lineOffset));
  }
  return routes;
}

function extractRouteObject(routeObject, parentPath, lineOffset) {
  const pathProperty = findObjectProperty(routeObject, "path");
  const nameProperty = findObjectProperty(routeObject, "name");
  const componentProperty = findObjectProperty(routeObject, "component");
  const childrenProperty = findObjectProperty(routeObject, "children");
  const routePath = joinRoutePath(parentPath, stringValue(pathProperty?.value) ?? "");
  const routeName = stringValue(nameProperty?.value);
  const component = componentSpecifier(componentProperty?.value);
  const routes = [];
  if (component) {
    routes.push({
      name: routeName,
      path: routePath,
      component,
      line: lineWithOffset(componentProperty?.loc?.start?.line || routeObject.loc?.start?.line, lineOffset)
    });
  }
  if (childrenProperty?.value?.type === "ArrayExpression") {
    routes.push(...extractRouteArray(childrenProperty.value, routePath, lineOffset));
  }
  return routes;
}

function findObjectProperty(objectExpression, name) {
  return (objectExpression.properties || []).find((property) => {
    if (property.type !== "ObjectProperty") {
      return false;
    }
    return propertyKeyName(property.key) === name;
  });
}

function propertyKeyName(key) {
  if (!key) {
    return null;
  }
  if (key.type === "Identifier") {
    return key.name;
  }
  if (key.type === "StringLiteral") {
    return key.value;
  }
  return null;
}

function propertyName(property) {
  if (!property) {
    return null;
  }
  if (property.type === "Identifier") {
    return property.name;
  }
  if (property.type === "StringLiteral") {
    return property.value;
  }
  return null;
}

function identifierName(node) {
  return node?.type === "Identifier" ? node.name : null;
}

function stringValue(node) {
  if (!node) {
    return null;
  }
  if (node.type === "StringLiteral") {
    return node.value;
  }
  if (node.type === "TemplateLiteral" && node.expressions.length === 0) {
    return node.quasis[0]?.value?.cooked ?? node.quasis[0]?.value?.raw ?? null;
  }
  return null;
}

function templateElementValue(node) {
  return node?.value?.cooked ?? node?.value?.raw ?? "";
}

function componentSpecifier(node) {
  if (!node) {
    return null;
  }
  if (node.type === "ArrowFunctionExpression" || node.type === "FunctionExpression") {
    return importCallSpecifier(node.body);
  }
  return importCallSpecifier(node);
}

function importCallSpecifier(node) {
  if (node?.type !== "CallExpression" || node.callee?.type !== "Import") {
    return null;
  }
  return node.arguments?.[0]?.type === "StringLiteral" ? node.arguments[0].value : null;
}

function joinRoutePath(parentPath, childPath) {
  if (childPath.startsWith("/")) {
    return normalizeRoutePath(childPath);
  }
  if (!childPath) {
    return normalizeRoutePath(parentPath || "/");
  }
  const base = parentPath && parentPath !== "/" ? parentPath : "";
  return normalizeRoutePath(`${base}/${childPath}`);
}

function normalizeRoutePath(routePath) {
  let normalized = routePath.replace(/\/+/g, "/");
  if (!normalized.startsWith("/")) {
    normalized = `/${normalized}`;
  }
  if (normalized.length > 1 && normalized.endsWith("/")) {
    normalized = normalized.slice(0, -1);
  }
  return normalized;
}

function lineWithOffset(line, lineOffset) {
  return Number.isInteger(line) ? line + lineOffset : null;
}

function walk(value, visit) {
  if (!value || typeof value !== "object") {
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      walk(item, visit);
    }
    return;
  }
  if (typeof value.type === "string") {
    visit(value);
  }
  for (const [key, child] of Object.entries(value)) {
    if (key === "loc" || key === "start" || key === "end") {
      continue;
    }
    walk(child, visit);
  }
}

function normalizeExtractorConfig(rawConfig) {
  const config = rawConfig && typeof rawConfig === "object" ? rawConfig : {};
  return {
    isVueRouterFile: config.isVueRouterFile === true,
    frontendApiCallsEnabled: config.frontendApiCallsEnabled !== false,
    playwrightEnabled: config.playwrightEnabled !== false,
    apiBase: normalizeApiBase(stringValueOrDefault(config.apiBase, "/api")),
    useApiFactoryNames: new Set(stringArrayOrDefault(config.useApiFactoryNames, ["useApi"])),
    apiClientNames: new Set(stringArrayOrDefault(config.apiClientNames, ["api", "apiClient"])),
    apiUrlHelperNames: new Set(stringArrayOrDefault(config.apiUrlHelperNames, ["getApiUrl"]))
  };
}

function stringArrayOrDefault(value, fallback) {
  if (!Array.isArray(value)) {
    return fallback;
  }
  return value.filter((item) => typeof item === "string" && item.length > 0);
}

function stringValueOrDefault(value, fallback) {
  return typeof value === "string" && value.length > 0 ? value : fallback;
}

function normalizeApiBase(value) {
  let normalized = value.replace(/\\/g, "/");
  if (!normalized.startsWith("/")) {
    normalized = `/${normalized}`;
  }
  normalized = normalized.replace(/\/+/g, "/");
  if (normalized.length > 1 && normalized.endsWith("/")) {
    normalized = normalized.slice(0, -1);
  }
  return normalized;
}
