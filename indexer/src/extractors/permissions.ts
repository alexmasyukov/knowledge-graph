import {
  SyntaxKind,
  ObjectLiteralExpression,
  PropertyAssignment,
  ArrayLiteralExpression,
  StringLiteral,
  Node,
} from 'ts-morph'
import path from 'node:path'

import type { LoadedProject } from '../project.js'

export interface PermissionRecord {
  key: string                    // dot-path, e.g. 'education.booking.read'
  roles: string[]                // ['ADMIN', 'EDUCATION_HEAD']
  file: string
  line: number
}

export interface PermissionsExtractResult {
  project: string
  permissions: PermissionRecord[]
  stats: {
    files: number
    permissionCount: number
    durationMs: number
  }
}

// ──────────────────────────────────────────────────────────────────────

function findPermissionsDecl(proj: LoadedProject): { obj: ObjectLiteralExpression; file: string } | null {
  for (const sf of proj.tsProject.getSourceFiles()) {
    const fp = sf.getFilePath()
    if (!fp.startsWith(proj.root)) continue
    if (fp.includes('node_modules')) continue
    if (!/\/common\/permissions\//.test(fp)) continue
    const decl = sf.getVariableDeclaration('PERMISSIONS')
    if (!decl) continue
    const init = decl.getInitializer()
    if (!init || init.getKind() !== SyntaxKind.ObjectLiteralExpression) continue
    return { obj: init as ObjectLiteralExpression, file: path.relative(proj.root, fp) }
  }
  return null
}

function unwrapAsExpression(node: Node): Node {
  // `{...} as PagePermissions` → unwrap to the inner object literal
  if (node.getKind() === SyntaxKind.AsExpression) {
    const inner = (node as any).getExpression?.() as Node | undefined
    if (inner) return inner
  }
  return node
}

function extractRolesArray(node: Node): string[] | null {
  const n = unwrapAsExpression(node)
  if (n.getKind() !== SyntaxKind.ArrayLiteralExpression) return null
  const arr = n as ArrayLiteralExpression
  const roles: string[] = []
  for (const el of arr.getElements()) {
    if (el.getKind() === SyntaxKind.StringLiteral) {
      roles.push((el as StringLiteral).getLiteralValue())
    }
  }
  return roles
}

function walk(
  obj: ObjectLiteralExpression,
  prefix: string[],
  file: string,
  out: PermissionRecord[],
): void {
  for (const prop of obj.getProperties()) {
    if (prop.getKind() !== SyntaxKind.PropertyAssignment) continue
    const pa = prop as PropertyAssignment
    const name = pa.getName()
    const init = pa.getInitializer()
    if (!init) continue

    const inner = unwrapAsExpression(init)

    // Leaf — string[] of roles
    if (inner.getKind() === SyntaxKind.ArrayLiteralExpression) {
      const roles = extractRolesArray(inner)
      if (roles) {
        out.push({
          key: [...prefix, name].join('.'),
          roles,
          file,
          line: pa.getStartLineNumber(),
        })
      }
      continue
    }

    // Nested object — recurse
    if (inner.getKind() === SyntaxKind.ObjectLiteralExpression) {
      walk(inner as ObjectLiteralExpression, [...prefix, name], file, out)
    }
  }
}

export function extractPermissions(proj: LoadedProject): PermissionsExtractResult {
  const t0 = Date.now()
  const found = findPermissionsDecl(proj)
  const out: PermissionRecord[] = []

  if (found) {
    walk(found.obj, [], found.file, out)
  }

  return {
    project: proj.name,
    permissions: out,
    stats: {
      files: found ? 1 : 0,
      permissionCount: out.length,
      durationMs: Date.now() - t0,
    },
  }
}
