import { Component } from '@angular/core'
import { NgbModal } from '@ng-bootstrap/ng-bootstrap'
import { CustomField, DATA_TYPE_LABELS } from 'src/app/data/custom-field'
import {
  CustomFieldQueryLogicalOperator,
  CustomFieldQueryOperator,
} from 'src/app/data/custom-field-query'
import { FILTER_CUSTOM_FIELDS_QUERY } from 'src/app/data/filter-rule-type'
import { DocumentListViewService } from 'src/app/services/document-list-view.service'
import {
  PermissionsService,
  PermissionType,
} from 'src/app/services/permissions.service'
import { CustomFieldsService } from 'src/app/services/rest/custom-fields.service'
import { ToastService } from 'src/app/services/toast.service'
import { CustomFieldEditDialogComponent } from '../../common/edit-dialog/custom-field-edit-dialog/custom-field-edit-dialog.component'
import { ManagementListComponent } from '../management-list/management-list.component'

@Component({
  selector: 'pngx-custom-fields-list',
  templateUrl: './../management-list/management-list.component.html',
  styleUrls: ['./../management-list/management-list.component.scss'],
})
export class CustomFieldsListComponent extends ManagementListComponent<CustomField> {
  permissionsDisabled = true

  constructor(
    customFieldsService: CustomFieldsService,
    modalService: NgbModal,
    toastService: ToastService,
    documentListViewService: DocumentListViewService,
    permissionsService: PermissionsService
  ) {
    super(
      customFieldsService,
      modalService,
      CustomFieldEditDialogComponent,
      toastService,
      documentListViewService,
      permissionsService,
      0, // see filterDocuments override below
      $localize`custom field`,
      $localize`custom fields`,
      PermissionType.CustomField,
      [
        {
          key: 'data_type',
          name: $localize`Data Type`,
          valueFn: (field: CustomField) => {
            return DATA_TYPE_LABELS.find((l) => l.id === field.data_type).name
          },
        },
      ]
    )
  }

  filterDocuments(field: CustomField) {
    this.documentListViewService.quickFilter([
      {
        rule_type: FILTER_CUSTOM_FIELDS_QUERY,
        value: JSON.stringify([
          CustomFieldQueryLogicalOperator.Or,
          [[field.id, CustomFieldQueryOperator.Exists, true]],
        ]),
      },
    ])
  }

  getDeleteMessage(object: CustomField) {
    return $localize`Do you really want to delete the field "${object.name}"?`
  }
}
