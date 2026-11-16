import type { Toolhead } from './types';

export function selectTool(tools: readonly Toolhead[] | null = []) {
  const heads = tools ?? [];
  const connected =
    heads.filter((tool) => tool.state === 'connected').length === 1
      ? heads.findIndex((tool) => tool.state === 'connected')
      : -1;
  return {
    connected,
    active: connected >= 0 ? connected : heads.findIndex((tool) => tool.state === 'changing'),
  };
}
