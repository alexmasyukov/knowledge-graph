export interface RouteRecord {
  /** resolved full path, e.g. /services/education/group/:id/edit */
  path: string
  /** is index route */
  index: boolean
  /** depth in tree */
  depth: number
  /** parent path (or null for root) */
  parentPath: string | null
  /** file:line of declaration */
  file: string
  line: number
  /** component identifier names found in element JSX (after stripping guards/Lazy*) */
  components: string[]
  /** guard component names */
  guards: string[]
  /** permission keys, e.g. 'education.schedule.read' */
  permissions: string[]
}

export interface ComponentRecord {
  name: string
  file: string
  line: number
  exported: boolean
  /** GqlHook names imported and used */
  hookCalls: string[]
  /** GqlOperation symbols referenced */
  operationRefs: string[]
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

export interface JsxAnalysis {
  guards: string[]
  permissions: string[]
  components: string[]
}
