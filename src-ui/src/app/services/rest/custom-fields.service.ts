import { HttpClient } from '@angular/common/http'
import { Injectable } from '@angular/core'
import { CustomField } from 'src/app/data/custom-field'
import { AbstractNameFilterService } from './abstract-name-filter-service'

@Injectable({
  providedIn: 'root',
})
export class CustomFieldsService extends AbstractNameFilterService<CustomField> {
  constructor(http: HttpClient) {
    super(http, 'custom_fields')
  }
}
