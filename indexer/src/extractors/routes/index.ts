import { SyntaxKind, SourceFile, ArrayLiteralExpression } from 'ts-morph'
import path from 'node:path'

import type { LoadedProject } from '../../project.js'
import { walkRoutesArray } from './tree.js'
import {
  resolveLazyTargetFile,
  resolveEagerImport,
  scanComponentForGql,
} from './components.js'
import type { ComponentRecord, RouteRecord, RoutesExtractResult } from './types.js'

export type { RouteRecord, ComponentRecord, RoutesExtractResult } from './types.js'

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

    const routesDecl = sf.getVariableDeclaration('routes')
    if (!routesDecl) continue
    const init = routesDecl.getInitializer()
    if (!init || init.getKind() !== SyntaxKind.ArrayLiteralExpression) continue

    walkRoutesArray(init as ArrayLiteralExpression, null, 0, rel, routes)

    // Resolve every referenced component (lazy first, eager fallback)
    for (const r of routes) {
      for (const compName of r.components) {
        if (components.has(compName)) continue
        const target =
          resolveLazyTargetFile(sf, compName, proj) ??
          resolveEagerImport(sf, compName, proj)
        if (!target) continue
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

    // Scan each component's file for gql references
    for (const comp of components.values()) {
      const abs = path.join(proj.root, comp.file)
      const compSf = proj.tsProject.getSourceFile(abs)
      if (!compSf) continue
      const { hookCalls, operationRefs } = scanComponentForGql(
        compSf,
        opts.knownHooks,
        opts.knownOperations,
      )
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
