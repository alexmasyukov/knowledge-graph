import {
  Node,
  SourceFile,
  SyntaxKind,
  TaggedTemplateExpression,
  VariableDeclaration,
  FunctionDeclaration,
  ArrowFunction,
  FunctionExpression,
} from 'ts-morph'
import { parse as parseGql, OperationDefinitionNode, FragmentDefinitionNode } from 'graphql'
import path from 'node:path'

import type { LoadedProject } from '../project.js'

export interface GqlOperationNode {
  /** Exported const name in TS, e.g. GET_EDUCATION_BOOKING */
  symbol: string
  /** GraphQL operation/fragment name, e.g. getEducationBooking */
  gqlName: string
  kind: 'query' | 'mutation' | 'subscription' | 'fragment'
  file: string
  line: number
  exported: boolean
}

export interface GqlHookNode {
  /** Exported function name, e.g. useEducationBooking */
  name: string
  file: string
  line: number
  /** TS-symbol of operations referenced in the hook body */
  operations: string[]
}

export interface CallsiteRef {
  /** Symbol being referenced: operation TS-name or hook name */
  target: string
  targetKind: 'operation' | 'hook'
  file: string
  line: number
  column: number
}

export interface GqlExtractResult {
  project: string
  operations: GqlOperationNode[]
  hooks: GqlHookNode[]
  callsites: CallsiteRef[]
  stats: {
    operationFiles: number
    hookFiles: number
    operationCount: number
    hookCount: number
    callsiteCount: number
    durationMs: number
  }
}

const GQL_TAG_NAMES = new Set(['gql'])

function relTo(root: string, file: string): string {
  return path.relative(root, file)
}

function isGqlTagged(t: TaggedTemplateExpression): boolean {
  const tag = t.getTag()
  return tag.getKind() === SyntaxKind.Identifier && GQL_TAG_NAMES.has(tag.getText())
}

function extractGqlBody(t: TaggedTemplateExpression): string {
  const template = t.getTemplate()
  // Concatenate the literal parts; placeholders (fragments via ${X}) become spread-fragments.
  if (template.getKind() === SyntaxKind.NoSubstitutionTemplateLiteral) {
    return template.getText().slice(1, -1)
  }
  // Template expression — strip ${...} placeholders for parser robustness.
  return template
    .getText()
    .replace(/\$\{[^}]+\}/g, '')
    .replace(/^`|`$/g, '')
}

function parseDocument(body: string): { ops: OperationDefinitionNode[]; frags: FragmentDefinitionNode[] } {
  try {
    const doc = parseGql(body, { noLocation: true })
    const ops: OperationDefinitionNode[] = []
    const frags: FragmentDefinitionNode[] = []
    for (const def of doc.definitions) {
      if (def.kind === 'OperationDefinition') ops.push(def)
      else if (def.kind === 'FragmentDefinition') frags.push(def)
    }
    return { ops, frags }
  } catch {
    return { ops: [], frags: [] }
  }
}

function findEnclosingVariable(t: TaggedTemplateExpression): VariableDeclaration | null {
  let p: Node | undefined = t.getParent()
  while (p) {
    if (p.getKind() === SyntaxKind.VariableDeclaration) return p as VariableDeclaration
    p = p.getParent()
  }
  return null
}

function isExported(decl: VariableDeclaration): boolean {
  const stmt = decl.getVariableStatement()
  return Boolean(stmt && stmt.isExported())
}

function* allFiles(proj: LoadedProject) {
  for (const sf of proj.tsProject.getSourceFiles()) {
    const fp = sf.getFilePath()
    if (fp.includes('node_modules')) continue
    if (!fp.startsWith(proj.root)) continue
    yield sf
  }
}

// ──────────────────────────────────────────────────────────────────────
// Operations
// ──────────────────────────────────────────────────────────────────────

function extractOperations(proj: LoadedProject): {
  operations: GqlOperationNode[]
  fileCount: number
  /** symbol → SourceFile, for hook ↔ operation linkage */
  symbolFiles: Map<string, { sf: SourceFile; decl: VariableDeclaration }>
} {
  const out: GqlOperationNode[] = []
  const symbolFiles = new Map<string, { sf: SourceFile; decl: VariableDeclaration }>()
  const seenFiles = new Set<string>()

  for (const sf of allFiles(proj)) {
    const fp = sf.getFilePath()
    if (!/\/gql\/queries\//.test(fp)) continue
    seenFiles.add(fp)

    sf.forEachDescendant((node) => {
      if (node.getKind() !== SyntaxKind.TaggedTemplateExpression) return
      const tte = node as TaggedTemplateExpression
      if (!isGqlTagged(tte)) return

      const decl = findEnclosingVariable(tte)
      if (!decl) return
      const symbol = decl.getName()
      const exported = isExported(decl)
      if (symbolFiles.has(symbol)) return // first wins
      symbolFiles.set(symbol, { sf, decl })

      const body = extractGqlBody(tte)
      const { ops, frags } = parseDocument(body)
      const line = decl.getStartLineNumber()

      if (ops.length === 0 && frags.length === 0) {
        // Unparseable — still record a stub node so callsites can land somewhere.
        out.push({
          symbol,
          gqlName: symbol,
          kind: 'fragment',
          file: relTo(proj.root, fp),
          line,
          exported,
        })
        return
      }

      for (const op of ops) {
        out.push({
          symbol,
          gqlName: op.name?.value ?? symbol,
          kind: op.operation,
          file: relTo(proj.root, fp),
          line,
          exported,
        })
      }
      for (const fr of frags) {
        out.push({
          symbol,
          gqlName: fr.name.value,
          kind: 'fragment',
          file: relTo(proj.root, fp),
          line,
          exported,
        })
      }
    })
  }

  return { operations: out, fileCount: seenFiles.size, symbolFiles }
}

// ──────────────────────────────────────────────────────────────────────
// Hooks
// ──────────────────────────────────────────────────────────────────────

function getHookFunctions(sf: SourceFile): { name: string; line: number; bodyNode: Node }[] {
  const found: { name: string; line: number; bodyNode: Node }[] = []

  // export function useFoo() {…}
  for (const fn of sf.getFunctions()) {
    if (!fn.isExported()) continue
    const name = fn.getName()
    if (!name || !/^use[A-Z]/.test(name)) continue
    const body = fn.getBody()
    if (!body) continue
    found.push({ name, line: fn.getStartLineNumber(), bodyNode: body })
  }

  // export const useFoo = () => {…}
  for (const vs of sf.getVariableStatements()) {
    if (!vs.isExported()) continue
    for (const d of vs.getDeclarations()) {
      const name = d.getName()
      if (!/^use[A-Z]/.test(name)) continue
      const init = d.getInitializer()
      if (!init) continue
      const k = init.getKind()
      if (k === SyntaxKind.ArrowFunction || k === SyntaxKind.FunctionExpression) {
        const body = (init as ArrowFunction | FunctionExpression).getBody()
        found.push({ name, line: d.getStartLineNumber(), bodyNode: body })
      }
    }
  }

  return found
}

function extractHooks(
  proj: LoadedProject,
  knownOperations: Set<string>,
): { hooks: GqlHookNode[]; fileCount: number; hookSymbols: Map<string, { sf: SourceFile; line: number }> } {
  const out: GqlHookNode[] = []
  const hookSymbols = new Map<string, { sf: SourceFile; line: number }>()
  const seenFiles = new Set<string>()

  for (const sf of allFiles(proj)) {
    const fp = sf.getFilePath()
    if (!/\/gql\/hooks\//.test(fp)) continue
    seenFiles.add(fp)

    for (const hook of getHookFunctions(sf)) {
      const operations = new Set<string>()

      hook.bodyNode.forEachDescendant((node) => {
        if (node.getKind() !== SyntaxKind.Identifier) return
        const text = node.getText()
        if (!knownOperations.has(text)) return
        operations.add(text)
      })

      out.push({
        name: hook.name,
        file: relTo(proj.root, fp),
        line: hook.line,
        operations: Array.from(operations).sort(),
      })
      hookSymbols.set(hook.name, { sf, line: hook.line })
    }
  }

  return { hooks: out, fileCount: seenFiles.size, hookSymbols }
}

// ──────────────────────────────────────────────────────────────────────
// Callsites
// ──────────────────────────────────────────────────────────────────────

function collectCallsites(
  proj: LoadedProject,
  operationSymbols: Map<string, { sf: SourceFile; decl: VariableDeclaration }>,
  hookSymbols: Map<string, { sf: SourceFile; line: number }>,
): CallsiteRef[] {
  const out: CallsiteRef[] = []
  const rootPrefix = proj.root + path.sep

  const handleRefs = (
    target: string,
    targetKind: 'operation' | 'hook',
    defFile: string,
    defLine: number,
    declNode: Node,
  ) => {
    const refs = declNode.findReferencesAsNodes()
    for (const node of refs) {
      const sf = node.getSourceFile()
      const fp = sf.getFilePath()
      if (!fp.startsWith(rootPrefix)) continue
      if (fp.includes('node_modules')) continue
      const { line, column } = sf.getLineAndColumnAtPos(node.getStart())
      if (relTo(proj.root, fp) === defFile && line === defLine) continue // skip the declaration itself
      out.push({
        target,
        targetKind,
        file: relTo(proj.root, fp),
        line,
        column,
      })
    }
  }

  for (const [symbol, { decl }] of operationSymbols) {
    const nameNode = decl.getNameNode()
    handleRefs(symbol, 'operation', relTo(proj.root, decl.getSourceFile().getFilePath()), decl.getStartLineNumber(), nameNode)
  }

  for (const [name, info] of hookSymbols) {
    const sf = info.sf
    // Find declaration node by name
    const decl =
      sf.getFunction(name) ??
      sf.getVariableDeclaration(name)
    if (!decl) continue
    handleRefs(name, 'hook', relTo(proj.root, sf.getFilePath()), info.line, decl.getNameNode() ?? decl)
  }

  return out
}

// ──────────────────────────────────────────────────────────────────────

export function extractGql(proj: LoadedProject): GqlExtractResult {
  const t0 = Date.now()

  const { operations, fileCount: opFiles, symbolFiles: opSymbolFiles } = extractOperations(proj)
  const knownOperationSymbols = new Set(opSymbolFiles.keys())
  const { hooks, fileCount: hookFiles, hookSymbols } = extractHooks(proj, knownOperationSymbols)
  const callsites = collectCallsites(proj, opSymbolFiles, hookSymbols)

  return {
    project: proj.name,
    operations,
    hooks,
    callsites,
    stats: {
      operationFiles: opFiles,
      hookFiles,
      operationCount: operations.length,
      hookCount: hooks.length,
      callsiteCount: callsites.length,
      durationMs: Date.now() - t0,
    },
  }
}
