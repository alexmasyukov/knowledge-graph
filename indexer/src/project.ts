import { Project } from 'ts-morph'
import path from 'node:path'
import fs from 'node:fs'

export interface ProjectInfo {
  name: string
  root: string
  tsconfig: string | null
  registeredAt: string
}

export interface LoadedProject extends ProjectInfo {
  tsProject: Project
}

export class ProjectRegistry {
  private projects = new Map<string, LoadedProject>()

  async register(name: string, root: string): Promise<ProjectInfo> {
    const absRoot = path.resolve(root)
    if (!fs.existsSync(absRoot)) {
      throw new Error(`Project root not found: ${absRoot}`)
    }

    const tsconfig = this.findTsconfig(absRoot)
    const tsProject = new Project({
      tsConfigFilePath: tsconfig ?? undefined,
      skipAddingFilesFromTsConfig: false,
      skipFileDependencyResolution: false,
    })

    const info: LoadedProject = {
      name,
      root: absRoot,
      tsconfig,
      registeredAt: new Date().toISOString(),
      tsProject,
    }
    this.projects.set(name, info)
    return this.toInfo(info)
  }

  get(name: string): LoadedProject {
    const p = this.projects.get(name)
    if (!p) throw new Error(`Project not registered: ${name}`)
    return p
  }

  list(): ProjectInfo[] {
    return Array.from(this.projects.values()).map((p) => this.toInfo(p))
  }

  private findTsconfig(root: string): string | null {
    const candidates = ['tsconfig.json', 'tsconfig.app.json', '../tsconfig.json']
    for (const c of candidates) {
      const p = path.join(root, c)
      if (fs.existsSync(p)) return p
    }
    return null
  }

  private toInfo(p: LoadedProject): ProjectInfo {
    const { tsProject: _tsProject, ...rest } = p
    return rest
  }
}
