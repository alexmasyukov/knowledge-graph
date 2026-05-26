import {
  Node,
  SyntaxKind,
  JsxElement,
  JsxSelfClosingElement,
  JsxOpeningElement,
  JsxFragment,
  Identifier,
  PropertyAccessExpression,
} from 'ts-morph'

import type { JsxAnalysis } from './types.js'

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

function getOpeningName(opening: JsxOpeningElement | JsxSelfClosingElement): string {
  return opening.getTagNameNode().getText()
}

/** PERMISSIONS.education.schedule.read → 'education.schedule.read' (strip PERMISSIONS root). */
function dotPathFromPropertyAccess(expr: Node): string | null {
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

function collectGuardPermissions(
  opening: JsxOpeningElement | JsxSelfClosingElement,
  acc: JsxAnalysis,
): void {
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

export function analyzeJsxElement(node: Node, acc: JsxAnalysis): void {
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
    node.forEachChild((c) => analyzeJsxElement(c, acc))
    return
  }

  node.forEachChild((c) => analyzeJsxElement(c, acc))
}
