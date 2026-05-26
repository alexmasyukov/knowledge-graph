import {
  Node,
  SyntaxKind,
  SourceFile,
  ObjectLiteralExpression,
  ArrayLiteralExpression,
  PropertyAssignment,
  StringLiteral,
  PropertyAccessExpression,
  TemplateExpression,
  NoSubstitutionTemplateLiteral,
} from 'ts-morph'

import { analyzeJsxElement } from './jsx.js'
import type { JsxAnalysis, RouteRecord } from './types.js'

function resolveEnumLikeExpression(expr: Node): string {
  if (expr.getKind() === SyntaxKind.PropertyAccessExpression) {
    const pa = expr as PropertyAccessExpression
    const propName = pa.getName()
    try {
      const sym = pa.getNameNode().getSymbol()
      if (sym) {
        for (const d of sym.getDeclarations()) {
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
    return propName.toLowerCase()
  }
  if (expr.getKind() === SyntaxKind.Identifier) {
    return expr.getText().toLowerCase()
  }
  // Last-resort placeholder kept so the original surface form survives
  // in the route path — better than dropping a segment silently.
  return `\${${expr.getText()}}`
}

function getStringPropFromObject(obj: ObjectLiteralExpression, name: string): string | null {
  const prop = obj.getProperty(name)
  if (!prop || prop.getKind() !== SyntaxKind.PropertyAssignment) return null
  const init = (prop as PropertyAssignment).getInitializer()
  if (!init) return null
  if (init.getKind() === SyntaxKind.StringLiteral) return (init as StringLiteral).getLiteralValue()
  if (init.getKind() === SyntaxKind.NoSubstitutionTemplateLiteral) {
    return (init as NoSubstitutionTemplateLiteral).getLiteralValue()
  }
  // PageMode.CREATE → resolve enum value
  if (init.getKind() === SyntaxKind.PropertyAccessExpression) {
    return resolveEnumLikeExpression(init)
  }
  // `:id/${PageMode.EDIT}` → "id/edit"
  if (init.getKind() === SyntaxKind.TemplateExpression) {
    const tpl = init as TemplateExpression
    let out = tpl.getHead().getLiteralText()
    for (const span of tpl.getTemplateSpans()) {
      out += resolveEnumLikeExpression(span.getExpression())
      out += span.getLiteral().getLiteralText()
    }
    return out
  }
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
  if (
    init.getKind() === SyntaxKind.JsxElement ||
    init.getKind() === SyntaxKind.JsxSelfClosingElement ||
    init.getKind() === SyntaxKind.JsxFragment
  ) {
    return init
  }
  if (init.getKind() === SyntaxKind.ParenthesizedExpression) {
    return (
      init.getFirstChildByKind(SyntaxKind.JsxElement) ??
      init.getFirstChildByKind(SyntaxKind.JsxSelfClosingElement) ??
      init.getFirstChildByKind(SyntaxKind.JsxFragment) ??
      null
    )
  }
  return null
}

function getChildrenArrFromObject(obj: ObjectLiteralExpression): ArrayLiteralExpression | null {
  const prop = obj.getProperty('children')
  if (!prop || prop.getKind() !== SyntaxKind.PropertyAssignment) return null
  const init = (prop as PropertyAssignment).getInitializer()
  if (!init || init.getKind() !== SyntaxKind.ArrayLiteralExpression) return null
  return init as ArrayLiteralExpression
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

export function walkRoutesArray(
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

    const childArr = getChildrenArrFromObject(obj)
    if (childArr) walkRoutesArray(childArr, fullPath, depth + 1, rel, out)
  }
}
