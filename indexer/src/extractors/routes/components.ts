import {
  Node,
  SyntaxKind,
  SourceFile,
  CallExpression,
  ArrowFunction,
  StringLiteral,
  PropertyAccessExpression,
  ts,
} from 'ts-morph'
import path from 'node:path'

import type { LoadedProject } from '../../project.js'
import type { ComponentRecord } from './types.js'

/** Use the TypeScript compiler's module resolver — same paths/baseUrl as `tsc`.
 *  Returns the absolute path of the resolved file, or null. */
function resolveModule(
  fromFile: string,
  moduleSpecifier: string,
  proj: LoadedProject,
): string | null {
  const compilerOptions = proj.tsProject.getCompilerOptions()
  const result = ts.resolveModuleName(
    moduleSpecifier,
    fromFile,
    compilerOptions,
    ts.sys,
  )
  return result.resolvedModule?.resolvedFileName ?? null
}

interface LazyTarget {
  file: string         // path relative to project root
  line: number
  exported: boolean
}

export function resolveLazyTargetFile(
  routerSf: SourceFile,
  componentName: string,
  proj: LoadedProject,
): LazyTarget | null {
  // const Schedule = React.lazy(() => import('@pages/...').then(m => ({ default: m.Schedule })))
  const decl = routerSf.getVariableDeclaration(componentName)
  if (!decl) return null
  const init = decl.getInitializer()
  if (!init || init.getKind() !== SyntaxKind.CallExpression) return null
  const lazyCall = init as CallExpression

  const lazyArrow = lazyCall.getArguments()[0]
  if (!lazyArrow || lazyArrow.getKind() !== SyntaxKind.ArrowFunction) return null
  const arrow = lazyArrow as ArrowFunction

  let importPath: string | null = null
  let namedExport: string | null = null

  // Find `import('...').then(...)`
  arrow.forEachDescendant((n) => {
    if (importPath) return
    if (n.getKind() !== SyntaxKind.CallExpression) return
    const ce = n as CallExpression
    const ex = ce.getExpression()
    if (ex.getKind() === SyntaxKind.ImportKeyword || ex.getText() === 'import') {
      const arg = ce.getArguments()[0]
      if (arg && arg.getKind() === SyntaxKind.StringLiteral) {
        importPath = (arg as StringLiteral).getLiteralValue()
      }
    }
  })

  // Extract m.Name from .then((m) => ({ default: m.Name }))
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

  const resolved = resolveModule(routerSf.getFilePath(), importPath, proj)
  if (!resolved) return null

  const targetFile = proj.tsProject.getSourceFile(resolved)
  if (!targetFile) return null

  if (namedExport) {
    const ex =
      targetFile.getVariableDeclaration(namedExport) ??
      targetFile.getFunction(namedExport) ??
      targetFile.getClass(namedExport)
    if (ex) {
      return {
        file: path.relative(proj.root, targetFile.getFilePath()),
        line: ex.getStartLineNumber(),
        exported: true,
      }
    }
  }

  return {
    file: path.relative(proj.root, targetFile.getFilePath()),
    line: 1,
    exported: true,
  }
}

/** Find the file/line for a component imported eagerly from the router. */
export function resolveEagerImport(
  routerSf: SourceFile,
  componentName: string,
  proj: LoadedProject,
): LazyTarget | null {
  const imp = routerSf.getImportDeclarations().find((d) =>
    d.getNamedImports().some((ni) => ni.getName() === componentName),
  )
  if (!imp) return null
  const targetSf = imp.getModuleSpecifierSourceFile()
  if (!targetSf) return null
  const decl =
    targetSf.getVariableDeclaration(componentName) ??
    targetSf.getFunction(componentName) ??
    targetSf.getClass(componentName)
  return {
    file: path.relative(proj.root, targetSf.getFilePath()),
    line: decl?.getStartLineNumber() ?? 1,
    exported: true,
  }
}

/** Scan a component file for references to known GqlHook names and GqlOperation symbols. */
export function scanComponentForGql(
  sf: SourceFile,
  knownHooks: Set<string>,
  knownOperations: Set<string>,
): Pick<ComponentRecord, 'hookCalls' | 'operationRefs'> {
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
