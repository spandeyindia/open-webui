import * as XLSX from 'xlsx';

export type ExcelColumn = { key: string; title: string };

export const exportRowsToExcel = <T extends object>(
	filename: string,
	columns: ExcelColumn[],
	rows: T[]
) => {
	const worksheet = XLSX.utils.aoa_to_sheet([
		columns.map((column) => column.title),
		...rows.map((row) => {
			const values = row as Record<string, unknown>;
			return columns.map((column) => values[column.key] ?? '');
		})
	]);
	worksheet['!cols'] = columns.map((column) => ({ wch: Math.max(14, Math.min(48, column.title.length + 12)) }));
	const workbook = XLSX.utils.book_new();
	XLSX.utils.book_append_sheet(workbook, worksheet, 'Report');
	XLSX.writeFile(workbook, `${filename}-${new Date().toISOString().slice(0, 10)}.xlsx`, { compression: true });
};
