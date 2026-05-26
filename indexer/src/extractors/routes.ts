import {
  Node,
  SyntaxKind,
  SourceFile,
  ObjectLiteralExpression,
  ArrayLiteralExpression,
  PropertyAssignment,
  VariableDeclaration,
  JsxElement,
  JsxSelfClosingElement,
  JsxOpeningElement,
  JsxFragment,
  CallExpression,
  Identifier,
  ArrowFunction,
  StringLiteral,
  PropertyAccessExpression,
} from 'ts-morph'
import path from 'node:path'

import type { LoadedProject } from '../project.js'

// ──────────────────────────────────────────────────────────────────────
// Public types
// ──────────────────────────────────────────────────────────────────────

export interface RouteRecord {
  path: string                  // resolved full path, e.g. /services/education/group/:id/edit
  index: boolean
  depth: number
  parentPath: string | null
  file: string
  line: number
  components: string[]          // component identifier names found in element JSX (after stripping guards/Lazy*)
  guards: string[]              // guard component names
  permissions: string[]         // dot-paths like 'education.schedule.read'
}

export interface ComponentRecord {
  name: string
  file: string
  line: number
  exported: boolean
  hookCalls: string[]           // GqlHook names imported and used
  operationRefs: string[]       // GqlOperation symbols referenced
}

export interface RoutesExtractResult {
  project: string
  routes: RouteRecord[]
  components: ComponentRecord[]
  stats: {
    routerFiles: number
    routeCount: number
    componentCount: number
    durationMs: number
  }
}

// ──────────────────────────────────────────────────────────────────────
// Heuristics
// ──────────────────────────────────────────────────────────────────────

const GUARD_NAMES = new Set([
  'AuthGuard',
  'RouterGuard',
  'RoleBasedGuard',
  'RoleBasedDisableGuard',
])
const LAYOUT_OR_LAZY_PATTERNS = [
  /^Lazy/,                      // LazyForm, LazyTable
  /^DashboardLayout$/,          // root layout
  /^Suspense$/,
]
const REACT_ROUTER_NATIVE = new Set(['Navigate', 'Outlet'])

function isLayoutOrWrapper(name: string): boolean {
  if (REACT_ROUTER_NATIVE.has(name)) return true
  return LAYOUT_OR_LAZY_PATTERNS.some((rx) => rx.test(name))
}

function isGuard(name: string): boolean {
  return GUARD_NAMES.has(name)
}

// ──────────────────────────────────────────────────────────────────────
// JSX walking
// ──────────────────────────────────────────────────────────────────────

interface JsxAnalysis {
  guards: string[]
  permissions: string[]
  components: string[]
}

function getOpeningName(opening: JsxOpeningElement | JsxSelfClosingElement): string {
  return opening.getTagNameNode().getText()
}

function dotPathFromPropertyAccess(expr: Node): string | null {
  // PERMISSIONS.education.schedule.read → 'education.schedule.read' (strip PERMISSIONS root)
  if (expr.getKind() !== SyntaxKind.PropertyAccessExpression) return null
  const parts: string[] = []
  let cur: Node | undefined = expr
  while (cur && cur.getKind() === SyntaxKind.PropertyAccessExpression) {
    const pa = cur as PropertyAccessExpression
    parts.unshift(pa.getName())
    cur = pa.getExpression()
  }
  if (cur && cur.getKind() === SyntaxKind.Identifier) {
    const root = (cur as Identifier).getText()
    if (root === 'PERMISSIONS') return parts.join('.')
    return [root, ...parts].join('.')
  }
  return parts.join('.')
}

function analyzeJsxElement(node: Node, acc: JsxAnalysis): void {
  const k = node.getKind()

  if (k === SyntaxKind.JsxSelfClosingElement) {
    const sc = node as JsxSelfClosingElement
    const name = getOpeningName(sc)
    if (isGuard(name)) {
      acc.guards.push(name)
      collectGuardPermissions(sc, acc)
    } else if (!isLayoutOrWrapper(name)) {
      acc.components.push(name)
    }
    return
  }

  if (k === SyntaxKind.JsxElement) {
    const el = node as JsxElement
    const opening = el.getOpeningElement()
    const name = getOpeningName(opening)

    if (isGuard(name)) {
      acc.guards.push(name)
      collectGuardPermissions(opening, acc)
    } else if (!isLayoutOrWrapper(name)) {
      acc.components.push(name)
    }

    // dive into children regardless
    for (const child of el.getJsxChildren()) {
      analyzeJsxElement(child, acc)
    }
    return
  }

  if (k === SyntaxKind.JsxFragment) {
    for (const child of (node as JsxFragment).getJsxChildren()) {
      analyzeJsxElement(child, acc)
    }
    return
  }

  if (k === SyntaxKind.JsxExpression) {
    // {expr} containers, recurse children
    node.forEachChild((c) => analyzeJsxElement(c, acc))
    return
  }

  // wrappers (parenthesized, etc.) — recurse
  node.forEachChild((c) => analyzeJsxElement(c, acc))
}

function collectGuardPermissions(opening: JsxOpeningElement | JsxSelfClosingElement, acc: JsxAnalysis): void {
  for (const attr of opening.getAttributes()) {
    if (attr.getKind() !== SyntaxKind.JsxAttribute) continue
    const ja = attr.asKindOrThrow(SyntaxKind.JsxAttribute)
    if (ja.getNameNode().getText() !== 'roles') continue
    const init = ja.getInitializer()
    if (!init || init.getKind() !== SyntaxKind.JsxExpression) continue
    const expr = init.getFirstChildByKind(SyntaxKind.PropertyAccessExpression)
    if (!expr) continue
    const dp = dotPathFromPropertyAccess(expr)
    if (dp) acc.permissions.push(dp)
  }
}

// ──────────────────────────────────────────────────────────────────────
// Route tree walking (object literal)
// ──────────────────────────────────────────────────────────────────────

function getStringPropFromObject(obj: ObjectLiteralExpression, name: string): string | null {
  const prop = obj.getProperty(name)
  if (!prop || prop.getKind() !== SyntaxKind.PropertyAssignment) return null
  const init = (prop as PropertyAssignment).getInitializer()
  if (!init) return null
  if (init.getKind() === SyntaxKind.StringLiteral) return (init as StringLiteral).getLiteralValue()
  // PageMode.CREATE → resolve PropertyAccessExpression
  if (init.getKind() === SyntaxKind.PropertyAccessExpression) {
    const pa = init as PropertyAccessExpression
    const name = pa.getName()
    // PageMode values: CREATE='create' (typical enum). We'll try to resolve via getType()/symbol; fallback to literal name lowercased.
    try {
      const sym = pa.getNameNode().getSymbol()
      if (sym) {
        const decls = sym.getDeclarations()
        for (const d of decls) {
          if (d.getKind() === SyntaxKind.EnumMember) {
            const enumMem = d.asKindOrThrow(SyntaxKind.EnumMember)
            const val = enumMem.getValue()
            if (typeof val === 'string') return val
          }
        }
      }
    } catch {
      // ignore
    }
    return name.toLowerCase()
  }
  // Template like `${X}` — fall back to literal text
  return init.getText().replace(/^['"`]|['"`]$/g, '')
}

function getBoolPropFromObject(obj: ObjectLiteralExpression, name: string): boolean {
  const prop = obj.getProperty(name)
  if (!prop || prop.getKind() !== SyntaxKind.PropertyAssignment) return false
  const init = (prop as PropertyAssignment).getInitializer()
  if (!init) return false
  return init.getKind() === SyntaxKind.TrueKeyword
}

function getJsxFromElementProp(obj: ObjectLiteralExpression): Node | null {
  const prop = obj.getProperty('element')
  if (!prop || prop.getKind() !== SyntaxKind.PropertyAssignment) return null
  const init = (prop as PropertyAssignment).getInitializer()
  if (!init) return null
  // <X />, <X>...</X>, or parenthesized JSX
  if (
    init.getKind() === SyntaxKind.JsxElement ||
    init.getKind() === SyntaxKind.JsxSelfClosingElement ||
    init.getKind() === SyntaxKind.JsxFragment
  ) {
    return init
  }
  if (init.getKind() === SyntaxKind.ParenthesizedExpression) {
    return init.getFirstChildByKind(SyntaxKind.JsxElement) ??
           init.getFirstChildByKind(SyntaxKind.JsxSelfClosingElement) ??
           init.getFirstChildByKind(SyntaxKind.JsxFragment) ??
           null
  }
  return null
}

function getChildrenFromObject(obj: ObjectLiteralExpression): ObjectLiteralExpression[] {
  const prop = obj.getProperty('children')
  if (!prop || prop.getKind() !== SyntaxKind.PropertyAssignment) return []
  const init = (prop as PropertyAssignment).getInitializer()
  if (!init || init.getKind() !== SyntaxKind.ArrayLiteralExpression) return []
  return (init as ArrayLiteralExpression).getElements()
    .filter((e) => e.getKind() === SyntaxKind.ObjectLiteralExpression) as ObjectLiteralExpression[]
}

function joinPath(parent: string | null, segment: string | null, index: boolean): string {
  if (!parent || parent === '') {
    if (segment == null) return '/'
    return segment.startsWith('/') ? segment : '/' + segment
  }
  if (index || segment == null) return parent
  if (segment.startsWith('/')) return segment
  const p = parent.endsWith('/') ? parent.slice(0, -1) : parent
  const s = segment.startsWith('/') ? segment.slice(1) : segment
  return `${p}/${s}`
}

function walkRoutesArray(
  arr: ArrayLiteralExpression,
  parentPath: string | null,
  depth: number,
  rel: (sf: SourceFile, line?: number) => { file: string; line: number },
  out: RouteRecord[],
): void {
  for (const el of arr.getElements()) {
    if (el.getKind() !== SyntaxKind.ObjectLiteralExpression) continue
    const obj = el as ObjectLiteralExpression
    const sf = obj.getSourceFile()
    const { file, line } = rel(sf, obj.getStartLineNumber())

    const seg = getStringPropFromObject(obj, 'path')
    const idx = getBoolPropFromObject(obj, 'index')
    const fullPath = joinPath(parentPath, seg, idx)

    const elementNode = getJsxFromElementProp(obj)
    const analysis: JsxAnalysis = { guards: [], permissions: [], components: [] }
    if (elementNode) analyzeJsxElement(elementNode, analysis)

    out.push({
      path: fullPath,
      index: idx,
      depth,
      parentPath: parentPath ?? null,
      file,
      line,
      components: analysis.components,
      guards: Array.from(new Set(analysis.guards)),
      permissions: Array.from(new Set(analysis.permissions)),
    })

    const children = getChildrenFromObject(obj)
    if (children.length) {
      // synthesize a virtual array for the children
      const childArr = (obj.getProperty('children') as PropertyAssignment)
        .getInitializer() as ArrayLiteralExpression
      walkRoutesArray(childArr, fullPath, depth + 1, rel, out)
    }
  }
}

// ──────────────────────────────────────────────────────────────────────
// Component resolution: <Schedule /> → lazy import target file
// ──────────────────────────────────────────────────────────────────────

function resolveLazyTargetFile(routerSf: SourceFile, componentName: string, proj: LoadedProject): { file: string; line: number; exported: boolean } | null {
  // Find variable declaration in router file: const Schedule = React.lazy(() => import('@pages/...').then(m => ({ default: m.Schedule })))
  const decl = routerSf.getVariableDeclaration(componentName)
  if (!decl) return null
  const init = decl.getInitializer()
  if (!init || init.getKind() !== SyntaxKind.CallExpression) return null
  const lazyCall = init as CallExpression

  // Should be React.lazy(arrow)
  const lazyArrow = lazyCall.getArguments()[0]
  if (!lazyArrow || lazyArrow.getKind() !== SyntaxKind.ArrowFunction) return null
  // Inside: import('@pages/...').then(...) — find first CallExpression with no name (dynamic import) or 'import'
  const arrow = lazyArrow as ArrowFunction
  let importPath: string | null = null
  let namedExport: string | null = null

  arrow.forEachDescendant((n) => {
    if (importPath) return
    if (n.getKind() !== SyntaxKind.CallExpression) return
    const ce = n as CallExpression
    const ex = ce.getExpression()
    // dynamic import: ex is ImportKeyword
    if (ex.getKind() === SyntaxKind.ImportKeyword || ex.getText() === 'import') {
      const arg = ce.getArguments()[0]
      if (arg && arg.getKind() === SyntaxKind.StringLiteral) {
        importPath = (arg as StringLiteral).getLiteralValue()
      }
    }
  })

  // Try to extract m.Name from .then((m) => ({ default: m.Name }))
  arrow.forEachDescendant((n) => {
    if (namedExport) return
    if (n.getKind() !== SyntaxKind.PropertyAssignment) return
    const pa = n.asKindOrThrow(SyntaxKind.PropertyAssignment)
    if (pa.getName() !== 'default') return
    const v = pa.getInitializer()
    if (!v || v.getKind() !== SyntaxKind.PropertyAccessExpression) return
    namedExport = (v as PropertyAccessExpression).getName()
  })

  if (!importPath) return null

  // Resolve via TS resolution (uses tsconfig paths)
  const targetSf = proj.tsProject.getSourceFiles().find((sf) => {
    const fp = sf.getFilePath()
    // Try exact match using compiler module resolution
    return false
  })

  // Use TypeScript compiler resolution
  const resolved = resolveModuleFromRouter(routerSf, importPath, proj)
  if (!resolved) return null

  const targetFile = proj.tsProject.getSourceFile(resolved)
  if (!targetFile) return null

  // Find named export in target file
  if (namedExport) {
    const ex = targetFile.getVariableDeclaration(namedExport) ?? targetFile.getFunction(namedExport) ?? targetFile.getClass(namedExport)
    if (ex) {
      return {
        file: path.relative(proj.root, targetFile.getFilePath()),
        line: ex.getStartLineNumber(),
        exported: true,
      }
    }
  }

  // Fallback: default export or first exported function/component
  return {
    file: path.relative(proj.root, targetFile.getFilePath()),
    line: 1,
    exported: true,
  }
}

function resolveModuleFromRouter(routerSf: SourceFile, importPath: string, proj: LoadedProject): string | null {
  // ts-morph project knows about tsconfig paths. We synthesize an import declaration and use it.
  // Simpler path: ask the compiler via Program for module resolution.
  const program = proj.tsProject.getProgram().compilerObject
  const ts = (proj.tsProject as any).compilerObject?.constructor // ts module — not available easily
  // Fallback: try common candidates.
  const root = proj.root
  const candidates: string[] = []
  if (importPath.startsWith('@pages/')) {
    const rest = importPath.slice('@pages/'.length)
    candidates.push(path.join(root, 'src/pages', rest + '.tsx'))
    candidates.push(path.join(root, 'src/pages', rest + '.ts'))
    candidates.push(path.join(root, 'src/pages', rest, 'index.tsx'))
    candidates.push(path.join(root, 'src/pages', rest, 'index.ts'))
  } else if (importPath.startsWith('@')) {
    const m = importPath.match(/^@([^/]+)\/(.+)$/)
    if (m) {
      const alias = m[1]
      const rest = m[2]
      candidates.push(path.join(root, 'src', alias, rest + '.tsx'))
      candidates.push(path.join(root, 'src', alias, rest + '.ts'))
      candidates.push(path.join(root, 'src', alias, rest, 'index.tsx'))
      candidates.push(path.join(root, 'src', alias, rest, 'index.ts'))
    }
  } else if (importPath.startsWith('.')) {
    const base = path.resolve(path.dirname(routerSf.getFilePath()), importPath)
    candidates.push(base + '.tsx', base + '.ts', path.join(base, 'index.tsx'), path.join(base, 'index.ts'))
  }
  for (const c of candidates) {
    const sf = proj.tsProject.getSourceFile(c)
    if (sf) return c
  }
  return null
}

// ──────────────────────────────────────────────────────────────────────
// Component file scan: collect referenced GqlHook names and operation symbols
// (only those that match known symbols)
// ──────────────────────────────────────────────────────────────────────

function scanComponentForGql(
  sf: SourceFile,
  knownHooks: Set<string>,
  knownOperations: Set<string>,
): { hookCalls: string[]; operationRefs: string[] } {
  const hookCalls = new Set<string>()
  const operationRefs = new Set<string>()
  sf.forEachDescendant((node) => {
    if (node.getKind() !== SyntaxKind.Identifier) return
    const txt = node.getText()
    if (knownHooks.has(txt)) hookCalls.add(txt)
    if (knownOperations.has(txt)) operationRefs.add(txt)
  })
  return {
    hookCalls: Array.from(hookCalls).sort(),
    operationRefs: Array.from(operationRefs).sort(),
  }
}

// ──────────────────────────────────────────────────────────────────────
// Entry point
// ──────────────────────────────────────────────────────────────────────

export function extractRoutes(
  proj: LoadedProject,
  opts: { routerFiles: string[]; knownHooks: Set<string>; knownOperations: Set<string> },
): RoutesExtractResult {
  const t0 = Date.now()
  const routes: RouteRecord[] = []
  const components = new Map<string, ComponentRecord>()
  let routerFileCount = 0

  for (const relRouter of opts.routerFiles) {
    const abs = path.isAbsolute(relRouter) ? relRouter : path.join(proj.root, relRouter)
    const sf = proj.tsProject.getSourceFile(abs)
    if (!sf) continue
    routerFileCount++

    const rel = (s: SourceFile, line?: number) => ({
      file: path.relative(proj.root, s.getFilePath()),
      line: line ?? 1,
    })

    // Find `const routes = [...]`
    const routesDecl = sf.getVariableDeclaration('routes')
    if (!routesDecl) continue
    const init = routesDecl.getInitializer()
    if (!init || init.getKind() !== SyntaxKind.ArrayLiteralExpression) continue

    walkRoutesArray(init as ArrayLiteralExpression, null, 0, rel, routes)

    // Resolve every referenced component
    for (const r of routes) {
      for (const compName of r.components) {
        if (components.has(compName)) continue
        const target = resolveLazyTargetFile(sf, compName, proj)
        if (!target) {
          // Maybe it's imported directly (eager), not lazy
          const imp = sf.getImportDeclarations().find((d) =>
            d.getNamedImports().some((ni) => ni.getName() === compName),
          )
          if (imp) {
            const targetSf = imp.getModuleSpecifierSourceFile()
            if (targetSf) {
              const decl = targetSf.getVariableDeclaration(compName) ??
                           targetSf.getFunction(compName) ??
                           targetSf.getClass(compName)
              components.set(compName, {
                name: compName,
                file: path.relative(proj.root, targetSf.getFilePath()),
                line: decl?.getStartLineNumber() ?? 1,
                exported: true,
                hookCalls: [],
                operationRefs: [],
              })
              continue
            }
          }
          continue
        }
        components.set(compName, {
          name: compName,
          file: target.file,
          line: target.line,
          exported: target.exported,
          hookCalls: [],
          operationRefs: [],
        })
      }
    }

    // Scan component files for gql references
    for (const comp of components.values()) {
      const abs = path.join(proj.root, comp.file)
      const compSf = proj.tsProject.getSourceFile(abs)
      if (!compSf) continue
      const { hookCalls, operationRefs } = scanComponentForGql(compSf, opts.knownHooks, opts.knownOperations)
      comp.hookCalls = hookCalls
      comp.operationRefs = operationRefs
    }
  }

  return {
    project: proj.name,
    routes,
    components: Array.from(components.values()),
    stats: {
      routerFiles: routerFileCount,
      routeCount: routes.length,
      componentCount: components.size,
      durationMs: Date.now() - t0,
    },
  }
}
