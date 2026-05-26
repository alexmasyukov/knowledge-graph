import Fastify from 'fastify'
import { ProjectRegistry } from './project.js'
import { extractGql } from './extractors/gql.js'

const PORT = Number(process.env.INDEXER_PORT ?? 7401)
const HOST = process.env.INDEXER_HOST ?? '127.0.0.1'

const app = Fastify({ logger: { level: 'info' } })
const registry = new ProjectRegistry()

app.get('/health', async () => ({
  status: 'ok',
  service: 'kg-indexer',
  projects: registry.list(),
}))

app.post<{ Body: { name: string; root: string } }>('/projects/register', async (req) => {
  const { name, root } = req.body
  const info = await registry.register(name, root)
  return { ok: true, project: info }
})

app.get<{ Querystring: { project: string } }>('/projects/stats', async (req) => {
  const { project } = req.query
  const proj = registry.get(project)
  return {
    name: project,
    root: proj.root,
    sourceFiles: proj.tsProject.getSourceFiles().length,
  }
})

app.post<{ Body: { project: string } }>('/extract/gql', async (req) => {
  const { project } = req.body
  const proj = registry.get(project)
  return extractGql(proj)
})

app.listen({ port: PORT, host: HOST })
  .then(() => app.log.info(`kg-indexer listening on http://${HOST}:${PORT}`))
  .catch((err) => {
    app.log.error(err)
    process.exit(1)
  })
