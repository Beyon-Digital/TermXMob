import {json, request} from './api';

export type StudioSubject = {
  id: string; project_id: string | null; name: string; description: string;
  image_ids: string[]; created: number;
};
export function loadStudioSubjects(projectId: string | null, signal?: AbortSignal) {
  const query = projectId ? '?project_id=' + encodeURIComponent(projectId) : '';
  return request<StudioSubject[]>('/api/media/studio/subjects' + query, {signal});
}
export function createStudioSubject(projectId: string | null, name: string, description: string, imageIds: string[], signal?: AbortSignal) {
  return request<StudioSubject>('/api/media/studio/subjects', {
    ...json('POST', {project_id: projectId, name, description, image_ids: imageIds}), signal,
  });
}
export function deleteStudioSubject(id: string, signal?: AbortSignal) {
  return request<void>('/api/media/studio/subjects/' + encodeURIComponent(id), {...json('DELETE'), signal});
}
