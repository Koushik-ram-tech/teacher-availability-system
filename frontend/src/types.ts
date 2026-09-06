export type Level = 'UG' | 'PG';

export interface Program {
  id: string;
  name: string;
  level: Level;
  is_active: boolean;
}

export interface Teacher {
  id: string;
  name: string;
  acronym: string;
  level: Level;
  program_id: string;
  semester: number;
  department: string;
  is_active: boolean;
  program: Program;
}

export interface TeacherCreatePayload {
  name: string;
  acronym: string;
  level: Level;
  program_id: string;
  semester: number;
  department: string;
}
